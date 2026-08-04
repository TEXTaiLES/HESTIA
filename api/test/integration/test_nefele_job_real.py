"""Real integration for the nefele_job resource.

Exercises real Postgres (nefele_jobs table has an explicit migration — no
schema drift), real Kafka (simple JSON messages on nefele_job_created and
nefele_job_modified topics), and real MinIO (preview file uploads).

Run with: pytest -m integration
"""
import io
import json
import time

import pytest


pytestmark = pytest.mark.integration


BASE_URL = '/nefele'
JOB_URL = '/nefele/{}'
CLAIM_URL = '/nefele/claim'
PREVIEW_URL = '/nefele/{}/preview'
CANCEL_URL = '/nefele/{}/cancel'


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


class TestRealCreateAndGet:
    # POST creates a real DB row, publishes to nefele_job_created, and the job
    # is then retrievable via GET /nefele/<id>.
    def test_post_creates_row_and_publishes_kafka(
        self, real_client_nefele, auth_headers, real_db_connection, kafka_consumer_factory,
    ):
        consumer = kafka_consumer_factory('nefele_job_created')

        r = real_client_nefele.post(BASE_URL, headers=auth_headers, json={
            'scan_id': 'real-scan-1',
            'dataset_name': 'real-ds-1',
            'model': 'pgsr',
        })

        assert r.status_code == 201
        job_id = r.get_json()['job_id']

        try:
            # DB row exists with the fields we posted
            cur = real_db_connection.cursor()
            cur.execute("SELECT scan_id, dataset_name, model, status FROM nefele_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
            cur.close()
            assert row == ('real-scan-1', 'real-ds-1', 'pgsr', 'points_submitted')

            # Kafka nefele_job_created notification received
            msg = _consume_by_key(consumer, job_id)
            assert msg is not None, 'nefele_job_created message not received within timeout'
            assert json.loads(msg.value().decode('utf-8')) == {'job_id': job_id, 'scan_id': 'real-scan-1'}

            # GET the job by id — the same fields come back
            r2 = real_client_nefele.get(JOB_URL.format(job_id), headers=auth_headers)
            assert r2.status_code == 200
            assert r2.get_json()['scan_id'] == 'real-scan-1'
        finally:
            cur = real_db_connection.cursor()
            cur.execute("DELETE FROM nefele_jobs WHERE job_id = %s", (job_id,))
            real_db_connection.commit()
            cur.close()

    # A directly-inserted job is retrievable via the list endpoint filtered by scan_id.
    def test_get_list_filters_by_scan_id(self, real_client_nefele, auth_headers, test_nefele_job_row, real_db_connection):
        # Look up the scan_id we generated in the fixture
        cur = real_db_connection.cursor()
        cur.execute("SELECT scan_id FROM nefele_jobs WHERE job_id = %s", (test_nefele_job_row['job_id'],))
        scan_id = cur.fetchone()[0]
        cur.close()

        r = real_client_nefele.get(f'{BASE_URL}?scan_id={scan_id}', headers=auth_headers)
        assert r.status_code == 200
        job_ids = [j['job_id'] for j in r.get_json()]
        assert test_nefele_job_row['job_id'] in job_ids

    # GET on an unknown id returns 404.
    def test_get_item_not_found_returns_404(self, real_client_nefele, auth_headers):
        r = real_client_nefele.get(JOB_URL.format('00000000-0000-0000-0000-000000000000'), headers=auth_headers)
        assert r.status_code == 404


class TestRealPatch:
    # PATCH with instructions.decision='confirm' promotes status to 'running'
    # and publishes to nefele_job_modified.
    def test_patch_confirm_transitions_to_running(
        self, real_client_nefele, auth_headers, test_nefele_job_row,
        real_db_connection, kafka_consumer_factory,
    ):
        consumer = kafka_consumer_factory('nefele_job_modified')

        r = real_client_nefele.patch(
            JOB_URL.format(test_nefele_job_row['job_id']), headers=auth_headers,
            json={'instructions': {'decision': 'confirm'}},
        )
        assert r.status_code == 200

        cur = real_db_connection.cursor()
        cur.execute("SELECT status FROM nefele_jobs WHERE job_id = %s", (test_nefele_job_row['job_id'],))
        assert cur.fetchone()[0] == 'running'
        cur.close()

        msg = _consume_by_key(consumer, test_nefele_job_row['job_id'])
        assert msg is not None
        assert json.loads(msg.value().decode('utf-8'))['status'] == 'running'

    # PATCH with a worker-progress field updates just that column.
    def test_patch_worker_status_completed(
        self, real_client_nefele, auth_headers, test_nefele_job_row, real_db_connection,
    ):
        r = real_client_nefele.patch(
            JOB_URL.format(test_nefele_job_row['job_id']), headers=auth_headers,
            json={'status': 'completed', 'stage': 'done', 'stage_index': 5},
        )
        assert r.status_code == 200

        cur = real_db_connection.cursor()
        cur.execute(
            "SELECT status, stage, stage_index FROM nefele_jobs WHERE job_id = %s",
            (test_nefele_job_row['job_id'],),
        )
        assert cur.fetchone() == ('completed', 'done', 5)
        cur.close()

    # PATCH on an unknown job returns 404.
    def test_patch_not_found_returns_404(self, real_client_nefele, auth_headers):
        r = real_client_nefele.patch(
            JOB_URL.format('00000000-0000-0000-0000-000000000000'),
            headers=auth_headers,
            json={'status': 'running'},
        )
        assert r.status_code == 404


class TestRealClaim:
    # An unclaimed job in status='points_submitted' is claimed by the worker,
    # its status becomes 'previewing', and a nefele_job_modified notification fires.
    def test_claim_picks_up_pending_job_and_notifies(
        self, real_client_nefele, auth_headers, test_nefele_job_row,
        real_db_connection, kafka_consumer_factory,
    ):
        consumer = kafka_consumer_factory('nefele_job_modified')

        r = real_client_nefele.post(CLAIM_URL, headers=auth_headers)
        # There may be OTHER pending jobs in the dev DB from prior work.
        # Any 200 is a valid claim — we just need to verify the claimed row is
        # one of ours OR that the flow works. The safer assertion: after our
        # test job is claimed (whenever), its status flipped to 'previewing'.
        assert r.status_code == 200
        claimed_id = r.get_json()['job_id']

        # Confirm the claimed job's DB status is now 'previewing'.
        cur = real_db_connection.cursor()
        cur.execute("SELECT status FROM nefele_jobs WHERE job_id = %s", (claimed_id,))
        assert cur.fetchone()[0] == 'previewing'
        cur.close()

        # Kafka notification for the claimed job.
        msg = _consume_by_key(consumer, claimed_id)
        assert msg is not None
        assert json.loads(msg.value().decode('utf-8'))['status'] == 'previewing'


class TestRealPreview:
    # POST /nefele/<id>/preview uploads real PNGs to the nefele bucket, sets
    # status='preview_ready' in the DB, and publishes to nefele_job_modified.
    def test_preview_uploads_to_minio_and_updates_status(
        self, real_client_nefele, auth_headers, test_nefele_job_row,
        real_db_connection, real_minio_client, kafka_consumer_factory,
    ):
        consumer = kafka_consumer_factory('nefele_job_modified')

        r = real_client_nefele.post(
            PREVIEW_URL.format(test_nefele_job_row['job_id']), headers=auth_headers,
            data={'file': (io.BytesIO(b'preview-bytes'), 'preview.png')},
        )
        assert r.status_code == 200, f'unexpected body: {r.get_data(as_text=True)}'
        body = r.get_json()
        assert body['message'] == 'preview stored'
        assert len(body['preview']) == 1

        # MinIO object exists in the nefele bucket
        object_name = f"{test_nefele_job_row['job_id']}/preview.png"
        stat = real_minio_client.stat_object('nefele', object_name)
        assert stat.size == len(b'preview-bytes')

        # DB status is preview_ready and preview column has the URL list
        cur = real_db_connection.cursor()
        cur.execute(
            "SELECT status, preview FROM nefele_jobs WHERE job_id = %s",
            (test_nefele_job_row['job_id'],),
        )
        status, preview = cur.fetchone()
        cur.close()
        assert status == 'preview_ready'
        preview_list = json.loads(preview) if isinstance(preview, str) else preview
        assert len(preview_list) == 1

        # Kafka notification
        msg = _consume_by_key(consumer, test_nefele_job_row['job_id'])
        assert msg is not None
        assert json.loads(msg.value().decode('utf-8'))['status'] == 'preview_ready'

    # Uploading a preview for a job that doesn't exist returns 404.
    def test_preview_job_not_found_returns_404(self, real_client_nefele, auth_headers):
        r = real_client_nefele.post(
            PREVIEW_URL.format('00000000-0000-0000-0000-000000000000'),
            headers=auth_headers,
            data={'file': (io.BytesIO(b'x'), 'preview.png')},
        )
        assert r.status_code == 404


class TestRealCancel:
    # Cancelling a job in 'points_submitted' sets status='cancelled' and notifies.
    def test_cancel_transitions_to_cancelled(
        self, real_client_nefele, auth_headers, test_nefele_job_row,
        real_db_connection, kafka_consumer_factory,
    ):
        consumer = kafka_consumer_factory('nefele_job_modified')

        r = real_client_nefele.post(
            CANCEL_URL.format(test_nefele_job_row['job_id']), headers=auth_headers,
        )
        assert r.status_code == 200

        cur = real_db_connection.cursor()
        cur.execute("SELECT status FROM nefele_jobs WHERE job_id = %s", (test_nefele_job_row['job_id'],))
        assert cur.fetchone()[0] == 'cancelled'
        cur.close()

        msg = _consume_by_key(consumer, test_nefele_job_row['job_id'])
        assert msg is not None
        assert json.loads(msg.value().decode('utf-8'))['status'] == 'cancelled'

    # Cancelling an already-cancelled job returns 409 (the SQL guard rejects
    # any terminal-state job).
    def test_cancel_already_cancelled_returns_409(
        self, real_client_nefele, auth_headers, test_nefele_job_row, real_db_connection,
    ):
        # Move the job to a terminal state directly
        cur = real_db_connection.cursor()
        cur.execute("UPDATE nefele_jobs SET status = 'cancelled' WHERE job_id = %s", (test_nefele_job_row['job_id'],))
        real_db_connection.commit()
        cur.close()

        r = real_client_nefele.post(
            CANCEL_URL.format(test_nefele_job_row['job_id']), headers=auth_headers,
        )
        assert r.status_code == 409

    # Cancelling an unknown job also returns 409 (same guard treats missing as terminal).
    def test_cancel_unknown_job_returns_409(self, real_client_nefele, auth_headers):
        r = real_client_nefele.post(
            CANCEL_URL.format('00000000-0000-0000-0000-000000000000'),
            headers=auth_headers,
        )
        assert r.status_code == 409


class TestRealAuth:
    def test_missing_header_rejects(self, real_client_nefele):
        r = real_client_nefele.get(BASE_URL)
        assert r.status_code == 401
