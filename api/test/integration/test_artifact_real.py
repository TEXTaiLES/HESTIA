"""Real integration for the artifact resource.

Exercises real MinIO (POST upload) + real Kafka + real Schema Registry (Avro
publish) + real Postgres (all GETs; DB rows populated asynchronously by the
Kafka JDBC sink). No mocking of external services.

Test order in this file matters: POST runs first so the sink creates the
artifacts table if it doesn't yet exist. GET/aggregate tests then INSERT
their own rows directly.

Run with: pytest -m integration
"""
import io
import time

import pytest


pytestmark = pytest.mark.integration


LIST_URL = '/artifacts'
ITEM_URL = '/artifacts/{}'
AGGREGATE_URL = '/artefacts/{}'


# Polls a consumer until it receives a message whose key matches, or times out.
def _consume_by_key(consumer, key: str, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = consumer.poll(1.0)
        if msg is None or msg.error():
            continue
        if msg.key() and msg.key().decode('utf-8') == key:
            return msg
    return None


class TestRealPost:
    # Full pipeline: MinIO write + Kafka Avro publish + Kafka notification.
    # The DB row appears via the Kafka JDBC sink; we poll for it with a
    # generous timeout because sink processing is asynchronous.
    def test_post_uploads_to_minio_and_publishes_kafka_and_sink_writes_row(
        self, real_client_artifact, auth_headers, real_minio_client,
        real_db_connection, kafka_consumer_factory,
    ):
        consumer = kafka_consumer_factory('artifact_uploaded')

        r = real_client_artifact.post(
            LIST_URL, headers=auth_headers,
            data={
                'file': (io.BytesIO(b'real integration payload'), 'realtest.jpg'),
                'title': 'Real Integration Test',
                'drone_id': 'test-drone-int',
            },
            content_type='multipart/form-data',
        )

        assert r.status_code == 201, f'unexpected body: {r.get_data(as_text=True)}'
        item = r.get_json()['uploaded_files'][0]
        artifact_id = item['artifact_id']
        object_name = f'{artifact_id}/realtest.jpg'

        # MinIO object exists
        stat = real_minio_client.stat_object('artifacts', object_name)
        assert stat.size == len(b'real integration payload')

        # Kafka notification received on artifact_uploaded topic
        msg = _consume_by_key(consumer, artifact_id)
        assert msg is not None, 'artifact_uploaded message not received within timeout'

        # DB row appears via the JDBC sink (may take a few seconds; longer on
        # cold cache / first-ever run when the sink also has to CREATE the table).
        row = None
        deadline = time.time() + 30
        while time.time() < deadline and row is None:
            cur = real_db_connection.cursor()
            cur.execute("SELECT to_regclass('public.artifacts')")
            if cur.fetchone()[0] is not None:
                cur.execute(
                    "SELECT artifact_id, filename, drone_id FROM artifacts WHERE artifact_id = %s",
                    (artifact_id,),
                )
                row = cur.fetchone()
            cur.close()
            if row is None:
                time.sleep(0.5)

        assert row is not None, 'Row did not appear in artifacts table within 30s (sink slow or misconfigured)'
        assert row[1] == 'realtest.jpg'
        assert row[2] == 'test-drone-int'

        # Cleanup: MinIO object and DB row
        real_minio_client.remove_object('artifacts', object_name)
        cur = real_db_connection.cursor()
        cur.execute("DELETE FROM artifacts WHERE artifact_id = %s", (artifact_id,))
        real_db_connection.commit()
        cur.close()

    # No file field triggers the 400 branch before touching MinIO / Kafka.
    def test_post_no_file_returns_400(self, real_client_artifact, auth_headers):
        r = real_client_artifact.post(
            LIST_URL, headers=auth_headers, data={}, content_type='multipart/form-data',
        )
        assert r.status_code == 400


class TestRealGetList:
    # An INSERTed artifact is retrievable via GET /artifacts filtered by drone_id.
    def test_get_list_filters_by_drone_id(self, real_client_artifact, auth_headers, test_artifact_db_row):
        r = real_client_artifact.get(
            f"{LIST_URL}?drone_id={test_artifact_db_row['drone_id']}",
            headers=auth_headers,
        )
        assert r.status_code == 200
        ids = [a['artifact_id'] for a in r.get_json()]
        assert test_artifact_db_row['artifact_id'] in ids

    # ?fields=X projects the response to the requested columns.
    def test_get_list_fields_projection_narrows_columns(
        self, real_client_artifact, auth_headers, test_artifact_db_row,
    ):
        r = real_client_artifact.get(
            f"{LIST_URL}?drone_id={test_artifact_db_row['drone_id']}&fields=artifact_id,filename",
            headers=auth_headers,
        )
        assert r.status_code == 200
        for row in r.get_json():
            # Only the two requested columns should be present
            assert set(row.keys()) == {'artifact_id', 'filename'}


class TestRealGetItem:
    # An INSERTed artifact is retrievable by id via GET /artifacts/<id>.
    def test_get_item_returns_inserted_artifact(
        self, real_client_artifact, auth_headers, test_artifact_db_row,
    ):
        r = real_client_artifact.get(
            ITEM_URL.format(test_artifact_db_row['artifact_id']),
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['artifact_id'] == test_artifact_db_row['artifact_id']
        assert body['filename'] == 'test.jpg'
        assert body['drone_id'] == 'test-drone'

    # Unknown artifact id returns 404. Depends on test_artifact_db_row so that
    # the artifacts table exists (fixture skips if the sink hasn't created it yet).
    def test_get_item_not_found_returns_404(self, real_client_artifact, auth_headers, test_artifact_db_row):
        r = real_client_artifact.get(ITEM_URL.format('nonexistent-artifact-xyz'), headers=auth_headers)
        assert r.status_code == 404


class TestRealAggregate:
    # Existing artifact with no linked rows returns artifact + empty lists for
    # each related table (or missing-table fallback).
    def test_aggregate_returns_artifact_and_empty_related(
        self, real_client_artifact, auth_headers, test_artifact_db_row,
    ):
        r = real_client_artifact.get(
            AGGREGATE_URL.format(test_artifact_db_row['artifact_id']),
            headers=auth_headers,
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['artifact']['artifact_id'] == test_artifact_db_row['artifact_id']
        for key in ('sensor_readings', 'robot_images', 'reconstructions', 'annotations', 'nefele_jobs'):
            assert isinstance(body[key], list)

    # A linked reconstruction shows up under the 'reconstructions' key.
    def test_aggregate_includes_linked_reconstruction(
        self, real_client_artifact, auth_headers, test_artifact_db_row,
        real_db_connection,
    ):
        # Insert a reconstruction linked to this artifact
        recon_object_id = f'test-recon-agg-{test_artifact_db_row["artifact_id"][:6]}'
        cur = real_db_connection.cursor()
        cur.execute(
            "INSERT INTO reconstructions (object_id, scan_id, filename, glb_location, \"timestamp\", artifact_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (recon_object_id, 'test-scan', 'model.glb', 's3://reconstructions/x', '2026-01-01T00:00:00Z', test_artifact_db_row['artifact_id']),
        )
        real_db_connection.commit()
        cur.close()

        try:
            r = real_client_artifact.get(
                AGGREGATE_URL.format(test_artifact_db_row['artifact_id']),
                headers=auth_headers,
            )
            assert r.status_code == 200
            recons = r.get_json()['reconstructions']
            recon_object_ids = [row['object_id'] for row in recons]
            assert recon_object_id in recon_object_ids
        finally:
            cur = real_db_connection.cursor()
            cur.execute("DELETE FROM reconstructions WHERE object_id = %s", (recon_object_id,))
            real_db_connection.commit()
            cur.close()

    # Nonexistent artifact returns 404. Depends on test_artifact_db_row for the same
    # reason — the aggregate resource errors when the artifacts table doesn't exist.
    def test_aggregate_not_found_returns_404(self, real_client_artifact, auth_headers, test_artifact_db_row):
        r = real_client_artifact.get(AGGREGATE_URL.format('nonexistent-artifact-xyz'), headers=auth_headers)
        assert r.status_code == 404


class TestRealAuth:
    def test_missing_header_rejects(self, real_client_artifact):
        r = real_client_artifact.get(LIST_URL)
        assert r.status_code == 401
