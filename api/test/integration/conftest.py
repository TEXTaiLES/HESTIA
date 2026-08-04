import io
import json
import os
import socket
import time
import uuid

import psycopg2
import pytest
import requests
from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka.schema_registry import SchemaRegistryClient
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


# Kafka broker + Schema Registry endpoints. TCP checks upfront so downstream
# tests skip cleanly instead of erroring on first send. Requires `kafka` to
# resolve (add `127.0.0.1 kafka` to hosts on Windows).
@pytest.fixture(scope='session')
def kafka_config():
    broker = os.environ.get('TEST_KAFKA_BROKER', 'localhost:29092')
    schema_registry = os.environ.get('TEST_SCHEMA_REGISTRY', 'http://localhost:8081')

    try:
        with socket.create_connection(('localhost', 29092), timeout=3):
            pass
    except Exception as e:
        pytest.skip(f'Kafka broker not reachable at localhost:29092: {e}')

    try:
        requests.get(f'{schema_registry}/subjects', timeout=3).raise_for_status()
    except Exception as e:
        pytest.skip(f'Schema Registry not reachable at {schema_registry}: {e}')

    try:
        socket.gethostbyname('kafka')
    except Exception:
        pytest.skip('Kafka hostname does not resolve — add "127.0.0.1 kafka" to hosts')

    return {'broker': broker, 'schema_registry': schema_registry}


# Factory yielding a fresh Kafka consumer subscribed to specified topic(s),
# primed to only see messages published after the fixture returns
# (auto.offset.reset=latest + wait for partition assignment). Auto-closes
# all consumers on teardown.
@pytest.fixture()
def kafka_consumer_factory(kafka_config):
    created: list = []

    def _factory(topics):
        if isinstance(topics, str):
            topics = [topics]

        # Pre-create any missing topics — Kafka doesn't auto-create on subscribe,
        # so consumers can't get partition assignments for topics that don't exist yet.
        admin = AdminClient({'bootstrap.servers': kafka_config['broker']})
        existing = set(admin.list_topics(timeout=5).topics.keys())
        missing = [NewTopic(t, num_partitions=1, replication_factor=1) for t in topics if t not in existing]
        if missing:
            futures = admin.create_topics(missing)
            for topic_name, fut in futures.items():
                try:
                    fut.result(timeout=10)
                except Exception as e:
                    # Ignore "topic already exists" races
                    if 'exists' not in str(e).lower():
                        raise
            time.sleep(1.0)  # let metadata propagate before subscribe

        consumer = Consumer({
            'bootstrap.servers': kafka_config['broker'],
            'group.id': f'test-{uuid.uuid4().hex[:8]}',
            'auto.offset.reset': 'latest',
            'enable.auto.commit': False,
        })
        consumer.subscribe(topics)
        deadline = time.time() + 10
        while time.time() < deadline and not consumer.assignment():
            consumer.poll(0.5)
        if not consumer.assignment():
            consumer.close()
            pytest.fail(f'Kafka consumer failed to assign to {topics}')

        # Explicitly seek each partition to its current end offset. Without
        # this, `auto.offset.reset='latest'` only decides the position on the
        # first FETCH, not on assignment — leaving a race where a message
        # published between assignment and the first poll can be missed.
        for tp in consumer.assignment():
            _low, high = consumer.get_watermark_offsets(tp, timeout=5)
            consumer.seek(TopicPartition(tp.topic, tp.partition, high))

        created.append(consumer)
        return consumer

    yield _factory

    for c in created:
        c.close()


# Direct INSERT of an annotations row (used by GET/PATCH tests that need an
# existing row to read/update). Yields scene_id + object_id, deletes on teardown.
@pytest.fixture()
def test_annotation_row(real_db_connection):
    scene_id = f'test-scene-{uuid.uuid4().hex[:8]}'
    object_id = f'test-obj-{uuid.uuid4().hex[:8]}'

    cur = real_db_connection.cursor()
    cur.execute(
        "INSERT INTO annotations (scene_id, object_id, timestamp, collaborative, content, linked_objects) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (scene_id, object_id, '2026-01-01T00:00:00Z', False, json.dumps({'nodes': {}}), json.dumps({})),
    )
    real_db_connection.commit()
    cur.close()

    yield {'scene_id': scene_id, 'object_id': object_id}

    cur = real_db_connection.cursor()
    cur.execute("DELETE FROM annotations WHERE scene_id = %s", (scene_id,))
    real_db_connection.commit()
    cur.close()


# Flask test client wired against real Postgres + real Kafka + real Schema Registry.
# The schema_registry_client is rebuilt against localhost:8081 (the module-level
# one was built with the container-internal URL at import time).
@pytest.fixture()
def real_client_annotation(client, mocker, postgres_config, kafka_config):
    mocker.patch('services.database.PG_HOST', postgres_config['host'])
    mocker.patch('services.database.PG_PORT', postgres_config['port'])
    mocker.patch('services.database.PG_DB', postgres_config['database'])
    mocker.patch('services.database.PG_USER', postgres_config['user'])
    mocker.patch('services.database.PG_PASSWORD', postgres_config['password'])

    new_sr = SchemaRegistryClient({'url': kafka_config['schema_registry']})
    mocker.patch('services.messaging.schema_registry_client', new_sr)

    return client


# Wires the artifact resource against real Postgres + real Kafka/Schema
# Registry + real MinIO. Unlike Directus and DB (looked up freshly per call),
# the module-level minio_client was built with the container-internal endpoint
# at import time — has to be replaced with a client pointing at localhost.
@pytest.fixture()
def real_client_artifact(client, mocker, postgres_config, kafka_config, real_minio_client):
    mocker.patch('services.database.PG_HOST', postgres_config['host'])
    mocker.patch('services.database.PG_PORT', postgres_config['port'])
    mocker.patch('services.database.PG_DB', postgres_config['database'])
    mocker.patch('services.database.PG_USER', postgres_config['user'])
    mocker.patch('services.database.PG_PASSWORD', postgres_config['password'])

    new_sr = SchemaRegistryClient({'url': kafka_config['schema_registry']})
    mocker.patch('services.messaging.schema_registry_client', new_sr)

    mocker.patch('resources.artifact.minio_client', real_minio_client)

    return client


# Wires the nefele resource against real Postgres + real Kafka + real MinIO.
# nefele publishes only simple JSON messages (no Avro / no schema registry),
# but it does upload previews to MinIO — hence the client patch.
@pytest.fixture()
def real_client_nefele(client, mocker, postgres_config, kafka_config, real_minio_client):
    mocker.patch('services.database.PG_HOST', postgres_config['host'])
    mocker.patch('services.database.PG_PORT', postgres_config['port'])
    mocker.patch('services.database.PG_DB', postgres_config['database'])
    mocker.patch('services.database.PG_USER', postgres_config['user'])
    mocker.patch('services.database.PG_PASSWORD', postgres_config['password'])

    mocker.patch('resources.nefele_job.minio_client', real_minio_client)

    # kafka_config is a required dep so this fixture skips cleanly when Kafka
    # isn't reachable, matching the pattern of the other integration fixtures.
    _ = kafka_config
    return client


# Direct INSERT of a nefele_jobs row (nefele_jobs table is created by an
# explicit migration in setup_infrastructure.py, so it always exists).
# Yields job_id + created status, deletes on teardown. Any previews written
# to MinIO by the resource under test are also cleaned up.
@pytest.fixture()
def test_nefele_job_row(real_db_connection, real_minio_client):
    job_id = str(uuid.uuid4())

    cur = real_db_connection.cursor()
    cur.execute(
        "INSERT INTO nefele_jobs (job_id, scan_id, dataset_name, model, status) "
        "VALUES (%s, %s, %s, %s, %s)",
        (job_id, f'test-scan-{job_id[:6]}', f'test-ds-{job_id[:6]}', 'sugar', 'points_submitted'),
    )
    real_db_connection.commit()
    cur.close()

    yield {'job_id': job_id, 'status': 'points_submitted'}

    # Delete any preview objects the test uploaded under this job_id prefix.
    try:
        for obj in real_minio_client.list_objects('nefele', prefix=f'{job_id}/', recursive=True):
            real_minio_client.remove_object('nefele', obj.object_name)
    except Exception:
        pass

    cur = real_db_connection.cursor()
    cur.execute("DELETE FROM nefele_jobs WHERE job_id = %s", (job_id,))
    real_db_connection.commit()
    cur.close()


# Direct INSERT of an artifacts row for GET/aggregate tests. Skips if the
# artifacts table doesn't exist yet — the Kafka JDBC sink creates it lazily
# on first POST, so a bootstrap POST must have run at least once before this
# fixture can produce test data.
@pytest.fixture()
def test_artifact_db_row(real_db_connection):
    artifact_id = str(uuid.uuid4())

    cur = real_db_connection.cursor()
    cur.execute("SELECT to_regclass('public.artifacts')")
    if cur.fetchone()[0] is None:
        cur.close()
        pytest.skip('artifacts table does not exist — run POST integration first to let the sink create it')

    cur.execute(
        "INSERT INTO artifacts (artifact_id, filename, location, drone_id, title, uploaded_by, \"timestamp\") "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            artifact_id, 'test.jpg', f's3://artifacts/{artifact_id}/test.jpg',
            'test-drone', 'Test Artifact', 'test-user', '2026-01-01 00:00:00',
        ),
    )
    real_db_connection.commit()
    cur.close()

    yield {
        'artifact_id': artifact_id, 'filename': 'test.jpg',
        'drone_id': 'test-drone', 'title': 'Test Artifact',
    }

    cur = real_db_connection.cursor()
    cur.execute("DELETE FROM artifacts WHERE artifact_id = %s", (artifact_id,))
    real_db_connection.commit()
    cur.close()
