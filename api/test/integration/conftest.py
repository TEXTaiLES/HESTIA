import os
import uuid

import pytest
import requests
from dotenv import load_dotenv
from minio import Minio


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

