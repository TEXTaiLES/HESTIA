import io
import json

import pytest


BASE_URL = '/nefele'
JOB_URL = '/nefele/{}'
CLAIM_URL = '/nefele/claim'
PREVIEW_URL = '/nefele/{}/preview'
CANCEL_URL = '/nefele/{}/cancel'

JOB_ID = '11111111-2222-3333-4444-555555555555'
SCAN_ID = 'scan-abc'
DATASET_NAME = 'my-dataset'


# Mock (conn, cursor) supporting the `with get_db_connection() as conn, conn.cursor() as cur`
# pattern the resource uses everywhere. Row + description are configurable per test.
@pytest.fixture()
def mock_db(mocker):
    def _factory(fetchone=None, fetchall=None, description=None):
        conn = mocker.MagicMock(name='pg_conn')
        conn.__enter__.return_value = conn
        conn.__exit__.return_value = False

        cur = mocker.MagicMock(name='pg_cursor')
        cur.__enter__.return_value = cur
        cur.__exit__.return_value = False
        cur.fetchone.return_value = fetchone
        cur.fetchall.return_value = fetchall if fetchall is not None else []
        cur.description = description

        conn.cursor.return_value = cur

        for target in ('resources.nefele_job', 'resources.resource_base'):
            mocker.patch(f'{target}.get_db_connection', return_value=conn)
        return conn, cur
    return _factory


# Patches send_simple_message (the only Kafka helper this resource uses — no Avro).
@pytest.fixture()
def mock_kafka(mocker):
    return mocker.patch('resources.nefele_job.send_simple_message', return_value=True)


# Standard column set for a nefele_jobs row (matches the migration schema).
JOB_COLS = ('job_id', 'scan_id', 'dataset_name', 'model', 'status', 'artifact_id')
JOB_DESCRIPTION = [(c,) for c in JOB_COLS]


def _job_row(job_id=JOB_ID, scan_id=SCAN_ID, status='points_submitted'):
    return (job_id, scan_id, DATASET_NAME, 'sugar', status, None)


class TestAuth:
    # POST /nefele rejected without an Authorization header.
    def test_post_missing_header_returns_401(self, client):
        r = client.post(BASE_URL, json={'scan_id': SCAN_ID, 'dataset_name': DATASET_NAME})
        assert r.status_code == 401

    # GET /nefele/<id> rejected without an Authorization header.
    def test_get_item_missing_header_returns_401(self, client):
        r = client.get(JOB_URL.format(JOB_ID))
        assert r.status_code == 401

    # POST /nefele/claim rejected without an Authorization header.
    def test_claim_missing_header_returns_401(self, client):
        r = client.post(CLAIM_URL)
        assert r.status_code == 401

    # POST /nefele/<id>/preview rejected without an Authorization header.
    def test_preview_missing_header_returns_401(self, client):
        r = client.post(PREVIEW_URL.format(JOB_ID), data={'file': (io.BytesIO(b'x'), 'x.png')})
        assert r.status_code == 401

    # POST /nefele/<id>/cancel rejected without an Authorization header.
    def test_cancel_missing_header_returns_401(self, client):
        r = client.post(CANCEL_URL.format(JOB_ID))
        assert r.status_code == 401


class TestNefelePost:
    # Missing scan_id triggers the 400 branch without touching DB.
    def test_missing_scan_id_returns_400(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db()
        r = client.post(BASE_URL, headers=auth_headers, json={'dataset_name': DATASET_NAME})
        assert r.status_code == 400
        cur.execute.assert_not_called()
        mock_kafka.assert_not_called()

    # Missing dataset_name triggers the 400 branch without touching DB.
    def test_missing_dataset_name_returns_400(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db()
        r = client.post(BASE_URL, headers=auth_headers, json={'scan_id': SCAN_ID})
        assert r.status_code == 400
        cur.execute.assert_not_called()
        mock_kafka.assert_not_called()

    # Empty JSON body is missing both fields → 400.
    def test_empty_body_returns_400(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db()
        r = client.post(BASE_URL, headers=auth_headers, json={})
        assert r.status_code == 400
        cur.execute.assert_not_called()

    # A valid body with the default 'sugar' model creates the job and returns 201.
    def test_happy_path_default_model_returns_201(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=_job_row(), description=JOB_DESCRIPTION)
        r = client.post(BASE_URL, headers=auth_headers, json={
            'scan_id': SCAN_ID, 'dataset_name': DATASET_NAME,
        })
        assert r.status_code == 201
        body = r.get_json()
        assert body['message'] == 'nefele job created'
        assert 'job_id' in body
        assert body['data']['status'] == 'points_submitted'

    # An unsupported model value silently coerces to 'sugar'.
    def test_unknown_model_defaults_to_sugar(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=_job_row(), description=JOB_DESCRIPTION)
        client.post(BASE_URL, headers=auth_headers, json={
            'scan_id': SCAN_ID, 'dataset_name': DATASET_NAME, 'model': 'unknown-model',
        })
        insert_sql, insert_params = cur.execute.call_args_list[0].args
        assert insert_sql.startswith('INSERT INTO nefele_jobs')
        assert 'sugar' in insert_params

    # 'pgsr' is accepted as a valid alternative model.
    def test_pgsr_model_accepted(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=_job_row(), description=JOB_DESCRIPTION)
        client.post(BASE_URL, headers=auth_headers, json={
            'scan_id': SCAN_ID, 'dataset_name': DATASET_NAME, 'model': 'pgsr',
        })
        _, insert_params = cur.execute.call_args_list[0].args
        assert 'pgsr' in insert_params

    # points_json is serialized to JSON before being stored.
    def test_points_json_is_json_serialized(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=_job_row(), description=JOB_DESCRIPTION)
        points = [{'x': 1, 'y': 2}]
        client.post(BASE_URL, headers=auth_headers, json={
            'scan_id': SCAN_ID, 'dataset_name': DATASET_NAME, 'points_json': points,
        })
        _, insert_params = cur.execute.call_args_list[0].args
        assert json.dumps(points) in insert_params

    # After the INSERT succeeds, a nefele_job_created notification fires with the new job_id.
    def test_kafka_notification_fired_after_insert(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=_job_row(), description=JOB_DESCRIPTION)
        r = client.post(BASE_URL, headers=auth_headers, json={
            'scan_id': SCAN_ID, 'dataset_name': DATASET_NAME,
        })
        topic, key, value = mock_kafka.call_args.args
        assert topic == 'nefele_job_created'
        assert key == r.get_json()['job_id']
        assert value == {'job_id': r.get_json()['job_id'], 'scan_id': SCAN_ID}

    # DB failure surfaces as 500 and no Kafka notification is sent.
    def test_db_error_returns_500(self, client, auth_headers, mocker, mock_kafka):
        mocker.patch(
            'resources.nefele_job.get_db_connection',
            side_effect=Exception('db down'),
        )
        r = client.post(BASE_URL, headers=auth_headers, json={
            'scan_id': SCAN_ID, 'dataset_name': DATASET_NAME,
        })
        assert r.status_code == 500
        mock_kafka.assert_not_called()


class TestNefeleGet:
    # No filters + rows present → JSON list of dicts.
    def test_returns_list_of_jobs(self, client, auth_headers, mock_db):
        mock_db(fetchall=[_job_row()], description=JOB_DESCRIPTION)
        r = client.get(BASE_URL, headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json()[0]['job_id'] == JOB_ID

    # No rows returns an empty list.
    def test_empty_result_returns_empty_list(self, client, auth_headers, mock_db):
        mock_db(fetchall=[], description=JOB_DESCRIPTION)
        r = client.get(BASE_URL, headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json() == []

    # ?status=X appends "AND status = %s" and binds X.
    def test_status_filter_appended(self, client, auth_headers, mock_db):
        _, cur = mock_db(description=JOB_DESCRIPTION)
        client.get(f'{BASE_URL}?status=running', headers=auth_headers)
        sql, params = cur.execute.call_args.args
        assert 'AND status = %s' in sql
        assert 'running' in params

    # ?scan_id=X appends "AND scan_id = %s" and binds X.
    def test_scan_id_filter_appended(self, client, auth_headers, mock_db):
        _, cur = mock_db(description=JOB_DESCRIPTION)
        client.get(f'{BASE_URL}?scan_id={SCAN_ID}', headers=auth_headers)
        sql, params = cur.execute.call_args.args
        assert 'AND scan_id = %s' in sql
        assert SCAN_ID in params

    # Newest jobs come first.
    def test_orders_by_created_at_descending(self, client, auth_headers, mock_db):
        _, cur = mock_db(description=JOB_DESCRIPTION)
        client.get(BASE_URL, headers=auth_headers)
        sql, _ = cur.execute.call_args.args
        assert 'ORDER BY created_at DESC' in sql

    # No page/per_page → LIMIT 50 OFFSET 0 (defaults).
    def test_applies_default_pagination(self, client, auth_headers, mock_db):
        _, cur = mock_db(description=JOB_DESCRIPTION)
        client.get(BASE_URL, headers=auth_headers)
        sql, params = cur.execute.call_args.args
        assert 'LIMIT %s OFFSET %s' in sql
        assert params[-2:] == (50, 0)

    # page=3, per_page=10 → LIMIT 10 OFFSET 20.
    def test_applies_custom_pagination(self, client, auth_headers, mock_db):
        _, cur = mock_db(description=JOB_DESCRIPTION)
        client.get(f'{BASE_URL}?page=3&per_page=10', headers=auth_headers)
        _, params = cur.execute.call_args.args
        assert params[-2:] == (10, 20)


class TestNefeleJobGet:
    # Found job returns 200 with the row.
    def test_found_returns_200(self, client, auth_headers, mock_db):
        mock_db(fetchone=_job_row(), description=JOB_DESCRIPTION)
        r = client.get(JOB_URL.format(JOB_ID), headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json()['job_id'] == JOB_ID

    # Missing job returns 404.
    def test_not_found_returns_404(self, client, auth_headers, mock_db):
        mock_db(fetchone=None)
        r = client.get(JOB_URL.format(JOB_ID), headers=auth_headers)
        assert r.status_code == 404


class TestNefeleJobPatchUi:
    # Bad decision value in instructions is rejected before UPDATE runs.
    def test_bad_decision_returns_400(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db()
        r = client.patch(JOB_URL.format(JOB_ID), headers=auth_headers, json={
            'instructions': {'decision': 'garbage'},
        })
        assert r.status_code == 400
        cur.execute.assert_not_called()
        mock_kafka.assert_not_called()

    # decision='confirm' promotes the job to status='running'.
    def test_confirm_sets_status_running(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=(JOB_ID,))
        client.patch(JOB_URL.format(JOB_ID), headers=auth_headers, json={
            'instructions': {'decision': 'confirm'},
        })
        _, params = cur.execute.call_args.args
        assert 'running' in params
        _, _, value = mock_kafka.call_args.args
        assert value['status'] == 'running'

    # decision='use_existing' also promotes to status='running'.
    def test_use_existing_sets_status_running(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=(JOB_ID,))
        client.patch(JOB_URL.format(JOB_ID), headers=auth_headers, json={
            'instructions': {'decision': 'use_existing'},
        })
        _, params = cur.execute.call_args.args
        assert 'running' in params

    # decision='redo' rewinds the job to status='points_submitted'.
    def test_redo_sets_status_points_submitted(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=(JOB_ID,))
        client.patch(JOB_URL.format(JOB_ID), headers=auth_headers, json={
            'instructions': {'decision': 'redo'},
        })
        _, params = cur.execute.call_args.args
        assert 'points_submitted' in params
        _, _, value = mock_kafka.call_args.args
        assert value['status'] == 'points_submitted'


class TestNefeleJobPatchWorker:
    # Worker path: stage / message / status / error fields are applied verbatim.
    @pytest.mark.parametrize('field, value', [
        ('stage', 'training'),
        ('message', 'halfway there'),
        ('status', 'completed'),
        ('error', 'timeout'),
    ])
    def test_worker_field_applied(self, client, auth_headers, mock_db, mock_kafka, field, value):
        _, cur = mock_db(fetchone=(JOB_ID,))
        client.patch(JOB_URL.format(JOB_ID), headers=auth_headers, json={field: value})
        sql, params = cur.execute.call_args.args
        assert f'{field} = %s' in sql
        assert value in params

    # stage_index is coerced to int before being bound (guards against string params).
    def test_stage_index_coerced_to_int(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=(JOB_ID,))
        client.patch(JOB_URL.format(JOB_ID), headers=auth_headers, json={'stage_index': '7'})
        _, params = cur.execute.call_args.args
        assert 7 in params  # int, not '7'


class TestNefeleJobPatchCommon:
    # No updatable fields (empty body) is rejected before UPDATE.
    def test_empty_body_returns_400(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db()
        r = client.patch(JOB_URL.format(JOB_ID), headers=auth_headers, json={})
        assert r.status_code == 400
        cur.execute.assert_not_called()
        mock_kafka.assert_not_called()

    # An unknown-only body has no updatable fields → 400.
    def test_unknown_fields_only_returns_400(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db()
        r = client.patch(JOB_URL.format(JOB_ID), headers=auth_headers, json={'unknown_field': 'x'})
        assert r.status_code == 400
        cur.execute.assert_not_called()

    # UPDATE RETURNING nothing → 404 (job not found) and no Kafka notification.
    def test_not_found_returns_404(self, client, auth_headers, mock_db, mock_kafka):
        mock_db(fetchone=None)  # RETURNING job_id returns nothing
        r = client.patch(JOB_URL.format(JOB_ID), headers=auth_headers, json={'status': 'running'})
        assert r.status_code == 404
        mock_kafka.assert_not_called()

    # Happy path returns 200 and publishes a nefele_job_modified notification.
    def test_happy_path_returns_200_and_notifies(self, client, auth_headers, mock_db, mock_kafka):
        mock_db(fetchone=(JOB_ID,))
        r = client.patch(JOB_URL.format(JOB_ID), headers=auth_headers, json={'status': 'running'})
        assert r.status_code == 200
        topic, key, value = mock_kafka.call_args.args
        assert topic == 'nefele_job_modified'
        assert key == JOB_ID
        assert value == {'job_id': JOB_ID, 'status': 'running'}


class TestNefeleClaim:
    # No pending jobs available returns 204 (empty body) and no Kafka notification.
    def test_no_available_jobs_returns_204(self, client, auth_headers, mock_db, mock_kafka):
        mock_db(fetchone=None)
        r = client.post(CLAIM_URL, headers=auth_headers)
        assert r.status_code == 204
        mock_kafka.assert_not_called()

    # A claimed job returns 200 with the row and its new 'previewing' status is
    # published to nefele_job_modified.
    def test_claim_returns_row_and_notifies(self, client, auth_headers, mock_db, mock_kafka):
        mock_db(fetchone=_job_row(status='previewing'), description=JOB_DESCRIPTION)
        r = client.post(CLAIM_URL, headers=auth_headers)
        assert r.status_code == 200
        body = r.get_json()
        assert body['job_id'] == JOB_ID
        topic, _key, value = mock_kafka.call_args.args
        assert topic == 'nefele_job_modified'
        assert value['status'] == 'previewing'

    # The claim SQL uses SKIP LOCKED so concurrent workers don't collide.
    # The claim UPDATE takes no bound params, so call_args.args is (sql,).
    def test_claim_sql_uses_skip_locked(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=None)
        client.post(CLAIM_URL, headers=auth_headers)
        sql = cur.execute.call_args.args[0]
        assert 'FOR UPDATE SKIP LOCKED' in sql

    # DB failure surfaces as 500 and skips the Kafka notification.
    def test_db_error_returns_500(self, client, auth_headers, mocker, mock_kafka):
        mocker.patch(
            'resources.nefele_job.get_db_connection',
            side_effect=Exception('db down'),
        )
        r = client.post(CLAIM_URL, headers=auth_headers)
        assert r.status_code == 500
        mock_kafka.assert_not_called()


class TestNefelePreview:
    # No file field returns 400 before touching MinIO or DB.
    def test_no_file_returns_400(self, client, auth_headers, mocker, mock_db, mock_kafka):
        put = mocker.patch('resources.nefele_job.minio_client.put_object')
        _, cur = mock_db()
        r = client.post(PREVIEW_URL.format(JOB_ID), headers=auth_headers, data={})
        assert r.status_code == 400
        put.assert_not_called()
        cur.execute.assert_not_called()
        mock_kafka.assert_not_called()

    # Empty filename triggers the same 400 branch.
    def test_empty_filename_returns_400(self, client, auth_headers, mocker, mock_db, mock_kafka):
        put = mocker.patch('resources.nefele_job.minio_client.put_object')
        _, cur = mock_db()
        r = client.post(
            PREVIEW_URL.format(JOB_ID), headers=auth_headers,
            data={'file': (io.BytesIO(b'x'), '')},
        )
        assert r.status_code == 400
        put.assert_not_called()

    # Happy path uploads to MinIO, updates the job, and publishes the notification.
    def test_happy_path_stores_preview_and_notifies(self, client, auth_headers, mocker, mock_db, mock_kafka):
        put = mocker.patch('resources.nefele_job.minio_client.put_object')
        _, cur = mock_db(fetchone=(JOB_ID,))
        r = client.post(
            PREVIEW_URL.format(JOB_ID), headers=auth_headers,
            data={'file': (io.BytesIO(b'preview-bytes'), 'p.png')},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['message'] == 'preview stored'
        assert len(body['preview']) == 1
        # MinIO put args
        args, kwargs = put.call_args
        assert args[0] == 'nefele'  # MINIO_NEFELE_BUCKET
        assert args[1] == f'{JOB_ID}/p.png'
        assert args[3] == len(b'preview-bytes')  # length
        # UPDATE ran with the preview JSON. Note: status is a SQL literal here
        # (`status = 'preview_ready'` inline in the query), not a bound param.
        update_sql, update_params = cur.execute.call_args.args
        assert 'preview = %s' in update_sql
        assert "status = 'preview_ready'" in update_sql
        # Kafka notification
        topic, key, value = mock_kafka.call_args.args
        assert topic == 'nefele_job_modified'
        assert key == JOB_ID
        assert value == {'job_id': JOB_ID, 'status': 'preview_ready'}

    # UPDATE RETURNING nothing (unknown job) → 404 and no notification.
    def test_job_not_found_returns_404(self, client, auth_headers, mocker, mock_db, mock_kafka):
        mocker.patch('resources.nefele_job.minio_client.put_object')
        mock_db(fetchone=None)
        r = client.post(
            PREVIEW_URL.format(JOB_ID), headers=auth_headers,
            data={'file': (io.BytesIO(b'x'), 'p.png')},
        )
        assert r.status_code == 404
        mock_kafka.assert_not_called()

    # MinIO failure surfaces as 500 and skips DB + Kafka.
    def test_minio_failure_returns_500(self, client, auth_headers, mocker, mock_db, mock_kafka):
        mocker.patch(
            'resources.nefele_job.minio_client.put_object',
            side_effect=Exception('minio down'),
        )
        _, cur = mock_db()
        r = client.post(
            PREVIEW_URL.format(JOB_ID), headers=auth_headers,
            data={'file': (io.BytesIO(b'x'), 'p.png')},
        )
        assert r.status_code == 500
        cur.execute.assert_not_called()
        mock_kafka.assert_not_called()

    # Multiple previews upload as separate objects and end up in the response list.
    def test_multiple_files_all_uploaded(self, client, auth_headers, mocker, mock_db, mock_kafka):
        put = mocker.patch('resources.nefele_job.minio_client.put_object')
        mock_db(fetchone=(JOB_ID,))
        r = client.post(
            PREVIEW_URL.format(JOB_ID), headers=auth_headers,
            data={'file': [
                (io.BytesIO(b'a'), 'a.png'),
                (io.BytesIO(b'b'), 'b.png'),
            ]},
        )
        assert r.status_code == 200
        assert put.call_count == 2
        assert len(r.get_json()['preview']) == 2


class TestNefeleCancel:
    # Cancellable job → 200 + Kafka notification.
    def test_cancel_success_returns_200_and_notifies(self, client, auth_headers, mock_db, mock_kafka):
        mock_db(fetchone=(JOB_ID,))
        r = client.post(CANCEL_URL.format(JOB_ID), headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json() == {'message': 'cancelled', 'job_id': JOB_ID}
        topic, key, value = mock_kafka.call_args.args
        assert topic == 'nefele_job_modified'
        assert key == JOB_ID
        assert value == {'job_id': JOB_ID, 'status': 'cancelled'}

    # Missing or terminal job → 409 and no notification.
    def test_terminal_or_missing_returns_409(self, client, auth_headers, mock_db, mock_kafka):
        mock_db(fetchone=None)
        r = client.post(CANCEL_URL.format(JOB_ID), headers=auth_headers)
        assert r.status_code == 409
        mock_kafka.assert_not_called()

    # The UPDATE guards on the whitelist of cancellable states (points_submitted,
    # previewing, preview_ready, running) — verify this is enforced in SQL.
    def test_sql_guards_cancellable_states(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=(JOB_ID,))
        client.post(CANCEL_URL.format(JOB_ID), headers=auth_headers)
        sql, _ = cur.execute.call_args.args
        for state in ('points_submitted', 'previewing', 'preview_ready', 'running'):
            assert state in sql

    # DB failure surfaces as 500 and skips the Kafka notification.
    def test_db_error_returns_500(self, client, auth_headers, mocker, mock_kafka):
        mocker.patch(
            'resources.nefele_job.get_db_connection',
            side_effect=Exception('db down'),
        )
        r = client.post(CANCEL_URL.format(JOB_ID), headers=auth_headers)
        assert r.status_code == 500
        mock_kafka.assert_not_called()
