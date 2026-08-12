"""Real integration for the amalthai_dataset resource.

Hits real Postgres (amalthai_datasets table has an explicit migration) and
real MinIO (amalthai-datasets bucket). No Kafka. No Directus.

Run with: pytest -m integration
"""
import io
import time

import pytest


pytestmark = pytest.mark.integration


LIST_URL = '/amalthai/datasets'
ITEM_URL = '/amalthai/datasets/{}'
ARCHIVE_URL = '/amalthai/datasets/{}/archive'
BUCKET = 'amalthai-datasets'


class TestRealCreateAndGet:
    # POST creates a real row and it's retrievable via GET /amalthai/datasets/<id>.
    def test_post_creates_row_and_item_lookup_returns_it(
        self, real_client_amalthai_dataset, auth_headers, real_db_connection,
    ):
        owner = f'test-owner-{int(time.time() * 1000) % 1_000_000}'
        r = real_client_amalthai_dataset.post(LIST_URL, headers=auth_headers, json={
            'owner_slug': owner,
            'name': 'real-ds',
            'mode': 'classification',
            'num_classes': 10,
            'manifest': {'note': 'integration'},
        })
        assert r.status_code == 201
        dataset_id = r.get_json()['dataset_id']

        try:
            r2 = real_client_amalthai_dataset.get(ITEM_URL.format(dataset_id), headers=auth_headers)
            assert r2.status_code == 200
            body = r2.get_json()
            assert body['owner_slug'] == owner
            assert body['name'] == 'real-ds'
            assert body['num_classes'] == 10
        finally:
            cur = real_db_connection.cursor()
            cur.execute("DELETE FROM amalthai_datasets WHERE dataset_id = %s", (dataset_id,))
            real_db_connection.commit()
            cur.close()

    # POSTing the same (owner_slug, mode, name) twice upserts — same dataset_id both times.
    def test_post_upsert_returns_same_dataset_id(
        self, real_client_amalthai_dataset, auth_headers, real_db_connection,
    ):
        owner = f'test-owner-{int(time.time() * 1000) % 1_000_000}'
        body = {'owner_slug': owner, 'name': 'upsert-ds', 'mode': 'classification', 'num_classes': 5}

        r1 = real_client_amalthai_dataset.post(LIST_URL, headers=auth_headers, json=body)
        assert r1.status_code == 201
        first_id = r1.get_json()['dataset_id']

        try:
            # Second POST with same natural key + different num_classes → upsert
            r2 = real_client_amalthai_dataset.post(
                LIST_URL, headers=auth_headers,
                json={**body, 'num_classes': 20},
            )
            assert r2.status_code == 201
            assert r2.get_json()['dataset_id'] == first_id
            # The updated field persisted
            assert r2.get_json()['data']['num_classes'] == 20
        finally:
            cur = real_db_connection.cursor()
            cur.execute("DELETE FROM amalthai_datasets WHERE dataset_id = %s", (first_id,))
            real_db_connection.commit()
            cur.close()

    # A directly-inserted row shows up in the owner-scoped list.
    def test_get_list_returns_owner_datasets(
        self, real_client_amalthai_dataset, auth_headers, test_amalthai_dataset_row,
    ):
        r = real_client_amalthai_dataset.get(
            f"{LIST_URL}?owner_slug={test_amalthai_dataset_row['owner_slug']}",
            headers=auth_headers,
        )
        assert r.status_code == 200
        ids = [d['dataset_id'] for d in r.get_json()]
        assert test_amalthai_dataset_row['dataset_id'] in ids

    # GET without owner_slug returns 400 (required query param).
    def test_get_list_missing_owner_slug_returns_400(self, real_client_amalthai_dataset, auth_headers):
        r = real_client_amalthai_dataset.get(LIST_URL, headers=auth_headers)
        assert r.status_code == 400

    # Item lookup on an unknown id returns 404.
    def test_get_item_not_found_returns_404(self, real_client_amalthai_dataset, auth_headers):
        r = real_client_amalthai_dataset.get(
            ITEM_URL.format('00000000-0000-0000-0000-000000000000'), headers=auth_headers,
        )
        assert r.status_code == 404


class TestRealArchive:
    # POST /archive uploads a real file to MinIO and sets object_key/archive_url/size_bytes in DB.
    def test_post_archive_uploads_to_minio_and_updates_row(
        self, real_client_amalthai_dataset, auth_headers, test_amalthai_dataset_row,
        real_db_connection, real_minio_client,
    ):
        blob = b'this-is-the-dataset-archive-blob' * 4  # 128 bytes
        r = real_client_amalthai_dataset.post(
            ARCHIVE_URL.format(test_amalthai_dataset_row['dataset_id']),
            headers=auth_headers,
            data={'file': (io.BytesIO(blob), 'dataset.tar.gz'), 'content_hash': 'sha256-fake'},
        )
        assert r.status_code == 200, f'unexpected body: {r.get_data(as_text=True)}'
        body = r.get_json()
        assert body['object_key'] == f"{test_amalthai_dataset_row['dataset_id']}/dataset.tar.gz"
        assert body['size_bytes'] == len(blob)

        # MinIO object exists with the expected bytes
        stat = real_minio_client.stat_object(BUCKET, body['object_key'])
        assert stat.size == len(blob)

        # DB row updated with archive fields + content_hash
        cur = real_db_connection.cursor()
        cur.execute(
            "SELECT object_key, size_bytes, content_hash, status FROM amalthai_datasets WHERE dataset_id = %s",
            (test_amalthai_dataset_row['dataset_id'],),
        )
        row = cur.fetchone()
        cur.close()
        assert row[0] == body['object_key']
        assert row[1] == len(blob)
        assert row[2] == 'sha256-fake'
        assert row[3] == 'ready'

    # POST /archive without a file returns 400.
    def test_post_archive_no_file_returns_400(self, real_client_amalthai_dataset, auth_headers, test_amalthai_dataset_row):
        r = real_client_amalthai_dataset.post(
            ARCHIVE_URL.format(test_amalthai_dataset_row['dataset_id']),
            headers=auth_headers, data={},
        )
        assert r.status_code == 400

    # POST /archive for an unknown dataset returns 404 before uploading anything.
    def test_post_archive_dataset_not_found_returns_404(self, real_client_amalthai_dataset, auth_headers):
        r = real_client_amalthai_dataset.post(
            ARCHIVE_URL.format('00000000-0000-0000-0000-000000000000'),
            headers=auth_headers,
            data={'file': (io.BytesIO(b'x'), 'x.tar.gz')},
        )
        assert r.status_code == 404

    # GET /archive streams back the exact bytes that were uploaded.
    def test_get_archive_streams_uploaded_bytes(
        self, real_client_amalthai_dataset, auth_headers, test_amalthai_dataset_row,
    ):
        # Upload first
        blob = b'roundtrip-archive-payload'
        up = real_client_amalthai_dataset.post(
            ARCHIVE_URL.format(test_amalthai_dataset_row['dataset_id']),
            headers=auth_headers,
            data={'file': (io.BytesIO(blob), 'roundtrip.tar.gz')},
        )
        assert up.status_code == 200

        # Then download
        r = real_client_amalthai_dataset.get(
            ARCHIVE_URL.format(test_amalthai_dataset_row['dataset_id']), headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.data == blob
        assert 'roundtrip.tar.gz' in r.headers.get('Content-Disposition', '')

    # GET /archive for a dataset that has no archive uploaded → 404.
    def test_get_archive_no_object_key_returns_404(
        self, real_client_amalthai_dataset, auth_headers, test_amalthai_dataset_row,
    ):
        # test_amalthai_dataset_row was inserted with no object_key
        r = real_client_amalthai_dataset.get(
            ARCHIVE_URL.format(test_amalthai_dataset_row['dataset_id']), headers=auth_headers,
        )
        assert r.status_code == 404

    # GET /archive for an unknown dataset returns 404.
    def test_get_archive_dataset_not_found_returns_404(self, real_client_amalthai_dataset, auth_headers):
        r = real_client_amalthai_dataset.get(
            ARCHIVE_URL.format('00000000-0000-0000-0000-000000000000'), headers=auth_headers,
        )
        assert r.status_code == 404


class TestRealAuth:
    def test_missing_header_rejects(self, real_client_amalthai_dataset):
        r = real_client_amalthai_dataset.get(LIST_URL)
        assert r.status_code == 401
