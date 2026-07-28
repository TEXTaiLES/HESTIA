import io
import os
import uuid

import psycopg2
import pytest
import requests
from dotenv import load_dotenv
from minio import Minio
from PIL import Image


# Load MinIO credentials from docker/.env when running integration tests locally.
# Shell env vars already set take precedence over the file.
_ENV_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'docker', '.env')
)
load_dotenv(_ENV_FILE)


# Real Minio client pointing at the running dev instance. Session-scoped so
# we don't rebuild it per test. Overridable via env vars for CI.
@pytest.fixture(scope='session')
def real_minio_client():
    endpoint = os.environ.get('TEST_MINIO_ENDPOINT', 'localhost:9000')
    access_key = os.environ.get('MINIO_ROOT_USER')
    secret_key = os.environ.get('MINIO_ROOT_PASSWORD')

    if not access_key or not secret_key:
        pytest.skip('MINIO_ROOT_USER / MINIO_ROOT_PASSWORD not set — cannot run integration tests')

    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)

    # Fail fast if MinIO isn't reachable rather than making every test error.
    try:
        client.list_buckets()
    except Exception as e:
        pytest.skip(f'MinIO at {endpoint} not reachable: {e}')

    return client


# Creates a uniquely-named bucket for each test and cleans it up afterwards
# (removes all objects, then the bucket). Guarantees isolation from dev state.
@pytest.fixture()
def test_bucket(real_minio_client):
    bucket = f'test-file-proxy-{uuid.uuid4().hex[:8]}'
    real_minio_client.make_bucket(bucket)

    yield bucket

    for obj in real_minio_client.list_objects(bucket, recursive=True):
        real_minio_client.remove_object(bucket, obj.object_name)
    real_minio_client.remove_bucket(bucket)


# Flask test client for http requests to API where the resource-level minio_client is replaced with
# the real one. We patch at import time on the resource module because the
# module-level singleton in services.storage was built before we could set
# MINIO_* env vars (it's imported via api.py at conftest load time).
@pytest.fixture()
def real_client(client, real_minio_client, mocker):
    mocker.patch('resources.file_proxy.minio_client', real_minio_client)
    return client


# Directus credentials + local URL, resolved from env (docker/.env). Session-scoped
# so we only pay the lookup cost once. Skips the whole integration run if
# creds are missing.
@pytest.fixture(scope='session')
def directus_config():
    url = os.environ.get('TEST_DIRECTUS_URL', 'http://localhost:8055')
    email = os.environ.get('DIRECTUS_ADMIN_EMAIL')
    password = os.environ.get('DIRECTUS_ADMIN_PASSWORD')

    if not email or not password:
        pytest.skip('DIRECTUS_ADMIN_EMAIL / DIRECTUS_ADMIN_PASSWORD not set')

    return {'url': url, 'email': email, 'password': password}


# Session-scoped admin token for direct Directus API calls in fixture setup /
# teardown (creating and deleting test artefacts). Independent of the token
# services.directus fetches per-request.
@pytest.fixture(scope='session')
def directus_token(directus_config):
    try:
        resp = requests.post(
            f"{directus_config['url']}/auth/login",
            json={'email': directus_config['email'], 'password': directus_config['password']},
            timeout=10,
        )
    except Exception as e:
        pytest.skip(f"Directus at {directus_config['url']} not reachable: {e}")

    if not resp.ok:
        pytest.skip(f'Directus auth failed ({resp.status_code}): {resp.text[:200]}')

    return resp.json()['data']['access_token']


# Creates a fresh artefact in Directus for the test, yields its id, then
# deletes it. If the artefacts collection requires fields on create, this
# skips with a message showing what Directus rejected.
@pytest.fixture()
def test_artefact(directus_config, directus_token):
    headers = {'Authorization': f'Bearer {directus_token}'}
    create_url = f"{directus_config['url']}/items/artefacts"

    resp = requests.post(create_url, headers=headers, json={}, timeout=10)
    if not resp.ok:
        pytest.skip(
            f'Cannot create test artefact ({resp.status_code}): {resp.text[:300]}\n'
            f'The artefacts collection likely requires fields — update the fixture.'
        )

    artefact_id = resp.json()['data']['id']

    yield artefact_id

    requests.delete(
        f"{create_url}/{artefact_id}",
        headers=headers,
        timeout=10,
    )


# Flask test client where services.directus module-level constants are patched
# to point at the real dev instance. Unlike MinIO (whose client is built at
# import time), Directus service functions look up DIRECTUS_URL/EMAIL/PASSWORD
# freshly on each call, so patching the module attributes is enough.
@pytest.fixture()
def real_client_directus(client, mocker, directus_config):
    mocker.patch('services.directus.DIRECTUS_URL', directus_config['url'])
    mocker.patch('services.directus.DIRECTUS_ADMIN_EMAIL', directus_config['email'])
    mocker.patch('services.directus.DIRECTUS_ADMIN_PASSWORD', directus_config['password'])
    return client


# Local Postgres connection info. Reads POSTGRES_* from docker/.env; host is
# forced to localhost since tests run outside the compose network.
@pytest.fixture(scope='session')
def postgres_config():
    host = os.environ.get('TEST_PG_HOST', 'localhost')
    port = os.environ.get('TEST_PG_PORT', '5432')
    db = os.environ.get('POSTGRES_DB') or os.environ.get('PG_DB')
    user = os.environ.get('POSTGRES_USER') or os.environ.get('PG_USER')
    password = os.environ.get('POSTGRES_PASSWORD') or os.environ.get('PG_PASSWORD')

    if not (db and user and password):
        pytest.skip('POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD not set')

    return {'host': host, 'port': port, 'database': db, 'user': user, 'password': password}


# Shared psycopg2 connection used by fixtures for direct INSERT/DELETE of test
# rows (separate from the connections the resource opens via get_db_connection).
@pytest.fixture(scope='session')
def real_db_connection(postgres_config):
    try:
        conn = psycopg2.connect(**postgres_config)
    except Exception as e:
        pytest.skip(f'Postgres at {postgres_config["host"]}:{postgres_config["port"]} not reachable: {e}')

    yield conn

    conn.close()


# Inserts a reconstructions row with a unique object_id and a fake glb_location
# (render_glb_thumbnail is mocked, so the file at that path never needs to exist).
# Yields object_id + glb_location, deletes the row on teardown.
@pytest.fixture()
def test_reconstruction(real_db_connection):
    object_id = f'test-recon-{uuid.uuid4().hex[:8]}'
    scan_id = f'test-scan-{uuid.uuid4().hex[:8]}'
    glb_location = f's3://reconstructions/{object_id}/model.glb'

    cur = real_db_connection.cursor()
    cur.execute(
        "INSERT INTO reconstructions (object_id, scan_id, filename, glb_location, timestamp) "
        "VALUES (%s, %s, %s, %s, %s)",
        (object_id, scan_id, 'model.glb', glb_location, '2026-01-01T00:00:00Z'),
    )
    real_db_connection.commit()
    cur.close()

    yield {'object_id': object_id, 'glb_location': glb_location}

    cur = real_db_connection.cursor()
    cur.execute("DELETE FROM reconstructions WHERE object_id = %s", (object_id,))
    real_db_connection.commit()
    cur.close()


# Collects file_ids uploaded to Directus during a test and deletes them on
# teardown so /files doesn't accumulate test junk.
@pytest.fixture()
def directus_files_cleanup(directus_config, directus_token):
    file_ids: list[str] = []

    yield file_ids

    headers = {'Authorization': f'Bearer {directus_token}'}
    for fid in file_ids:
        try:
            requests.delete(f"{directus_config['url']}/files/{fid}", headers=headers, timeout=10)
        except Exception:
            pass


def _make_valid_png() -> bytes:
    """Minimal valid PNG (1x1 red pixel) — safe payload for Directus /files uploads."""
    buf = io.BytesIO()
    Image.new('RGB', (1, 1), color='red').save(buf, format='PNG')
    return buf.getvalue()


# Flask test client wired against real Postgres + real Directus, with
# render_glb_thumbnail mocked to skip the OpenGL/subprocess step. Returns a
# dict so tests can access the render mock (to force failures) and the
# synthetic PNG bytes (to assert what got uploaded).
@pytest.fixture()
def real_client_thumbnail(client, mocker, postgres_config, directus_config):
    mocker.patch('services.database.PG_HOST', postgres_config['host'])
    mocker.patch('services.database.PG_PORT', postgres_config['port'])
    mocker.patch('services.database.PG_DB', postgres_config['database'])
    mocker.patch('services.database.PG_USER', postgres_config['user'])
    mocker.patch('services.database.PG_PASSWORD', postgres_config['password'])

    mocker.patch('services.directus.DIRECTUS_URL', directus_config['url'])
    mocker.patch('services.directus.DIRECTUS_ADMIN_EMAIL', directus_config['email'])
    mocker.patch('services.directus.DIRECTUS_ADMIN_PASSWORD', directus_config['password'])

    png_bytes = _make_valid_png()
    render = mocker.patch('resources.thumbnail.render_glb_thumbnail', return_value=png_bytes)

    return {'client': client, 'render': render, 'png_bytes': png_bytes}
