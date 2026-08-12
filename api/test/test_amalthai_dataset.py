import io
import json

import pytest


LIST_URL = '/amalthai/datasets'
ITEM_URL = '/amalthai/datasets/{}'
ARCHIVE_URL = '/amalthai/datasets/{}/archive'

DATASET_ID = '11111111-2222-3333-4444-555555555555'
OWNER = 'owner-x'
NAME = 'my-dataset'
MODE = 'classification'


# Column set + tuple layout for a mocked amalthai_datasets row. Enough columns
# to satisfy the response dict without listing the full 17-column schema.
ROW_COLS = ('dataset_id', 'owner_slug', 'name', 'mode', 'status', 'object_key')
ROW_DESC = [(c,) for c in ROW_COLS]


def _row(dataset_id=DATASET_ID, status='ready', object_key=None):
    return (dataset_id, OWNER, NAME, MODE, status, object_key)


# Mock (conn, cur) supporting context-manager usage (which is what every
# `with get_db_connection() as conn, conn.cursor() as cur` block relies on).
# fetchone_seq lets a single test drive a sequence of fetchone calls — needed
# because POST /amalthai/datasets does RETURNING then a follow-up SELECT, and
# archive POST does two separate `with get_db_connection()` blocks.
@pytest.fixture()
def mock_db(mocker):
    def _factory(fetchone=None, fetchone_seq=None, fetchall=None, description=None):
        conn = mocker.MagicMock(name='pg_conn')
        conn.__enter__.return_value = conn
        conn.__exit__.return_value = False

        cur = mocker.MagicMock(name='pg_cursor')
        cur.__enter__.return_value = cur
        cur.__exit__.return_value = False
        cur.fetchall.return_value = fetchall if fetchall is not None else []
        if fetchone_seq is not None:
            cur.fetchone.side_effect = fetchone_seq
        else:
            cur.fetchone.return_value = fetchone
        cur.description = description

        conn.cursor.return_value = cur
        mocker.patch('resources.amalthai_dataset.get_db_connection', return_value=conn)
        return conn, cur
    return _factory


# Patches upload_filestorage + stream_object at the resource-module level.
# These are the two amalthai_common helpers this resource uses; mocking them
# directly avoids having to also mock minio_client.put_object / get_object.
@pytest.fixture()
def mock_storage(mocker):
    upload = mocker.patch('resources.amalthai_dataset.upload_filestorage', return_value=1234)
    stream = mocker.patch('resources.amalthai_dataset.stream_object')
    return {'upload': upload, 'stream': stream}


class TestAuth:
    # POST /amalthai/datasets rejected without Authorization header.
    def test_post_missing_header_returns_401(self, client):
        r = client.post(LIST_URL, json={'owner_slug': OWNER, 'name': NAME, 'mode': MODE})
        assert r.status_code == 401

    # GET /amalthai/datasets rejected without Authorization header.
    def test_get_list_missing_header_returns_401(self, client):
        r = client.get(LIST_URL)
        assert r.status_code == 401

    # GET /amalthai/datasets/<id> rejected without Authorization header.
    def test_get_item_missing_header_returns_401(self, client):
        r = client.get(ITEM_URL.format(DATASET_ID))
        assert r.status_code == 401

    # POST /amalthai/datasets/<id>/archive rejected without Authorization header.
    def test_post_archive_missing_header_returns_401(self, client):
        r = client.post(
            ARCHIVE_URL.format(DATASET_ID),
            data={'file': (io.BytesIO(b'x'), 'x.tar.gz')},
        )
        assert r.status_code == 401

    # GET /amalthai/datasets/<id>/archive rejected without Authorization header.
    def test_get_archive_missing_header_returns_401(self, client):
        r = client.get(ARCHIVE_URL.format(DATASET_ID))
        assert r.status_code == 401


class TestDatasetPost:
    def _valid_body(self, **overrides):
        body = {'owner_slug': OWNER, 'name': NAME, 'mode': MODE}
        body.update(overrides)
        return body

    # Missing any of the three required fields returns 400 without touching DB.
    @pytest.mark.parametrize('missing', ['owner_slug', 'name', 'mode'])
    def test_missing_required_field_returns_400(self, client, auth_headers, mock_db, missing):
        _, cur = mock_db()
        body = self._valid_body()
        del body[missing]
        r = client.post(LIST_URL, headers=auth_headers, json=body)
        assert r.status_code == 400
        cur.execute.assert_not_called()

    # Empty body triggers the same 400 branch (all three required fields missing).
    def test_empty_body_returns_400(self, client, auth_headers, mock_db):
        _, cur = mock_db()
        r = client.post(LIST_URL, headers=auth_headers, json={})
        assert r.status_code == 400
        cur.execute.assert_not_called()

    # Happy path: INSERT ... RETURNING dataset_id → SELECT the full row → 201.
    def test_happy_path_returns_201(self, client, auth_headers, mock_db):
        _, cur = mock_db(
            fetchone_seq=[(DATASET_ID,), _row()],  # RETURNING, then _fetch_dataset SELECT
            description=ROW_DESC,
        )
        r = client.post(LIST_URL, headers=auth_headers, json=self._valid_body())
        assert r.status_code == 201
        body = r.get_json()
        assert body['message'] == 'dataset registered'
        assert body['dataset_id'] == DATASET_ID
        assert body['data']['owner_slug'] == OWNER
        assert body['data']['name'] == NAME
        assert body['data']['mode'] == MODE

    # The upsert INSERT uses ON CONFLICT to keep (owner_slug, mode, name) unique.
    # POST executes two statements (INSERT + follow-up SELECT); call_args_list[0] is the INSERT.
    def test_insert_uses_upsert(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchone_seq=[(DATASET_ID,), _row()], description=ROW_DESC)
        client.post(LIST_URL, headers=auth_headers, json=self._valid_body())
        sql, _ = cur.execute.call_args_list[0].args
        assert 'INSERT INTO amalthai_datasets' in sql
        assert 'ON CONFLICT (owner_slug, mode, name) DO UPDATE' in sql

    # A manifest dict is JSON-serialized before being bound to the INSERT.
    def test_manifest_json_serialized(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchone_seq=[(DATASET_ID,), _row()], description=ROW_DESC)
        manifest = {'classes': ['a', 'b'], 'count': 42}
        client.post(LIST_URL, headers=auth_headers, json=self._valid_body(manifest=manifest))
        _, params = cur.execute.call_args_list[0].args
        assert json.dumps(manifest) in params

    # Status defaults to 'ready' when not provided in the request body.
    def test_default_status_ready_applied(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchone_seq=[(DATASET_ID,), _row()], description=ROW_DESC)
        client.post(LIST_URL, headers=auth_headers, json=self._valid_body())
        _, params = cur.execute.call_args_list[0].args
        assert 'ready' in params

    # DB failure surfaces as 500 with an error body.
    def test_db_error_returns_500(self, client, auth_headers, mocker):
        mocker.patch(
            'resources.amalthai_dataset.get_db_connection',
            side_effect=Exception('db down'),
        )
        r = client.post(LIST_URL, headers=auth_headers, json=self._valid_body())
        assert r.status_code == 500


class TestDatasetGetList:
    # owner_slug is required; missing it returns 400 before any query runs.
    def test_missing_owner_slug_returns_400(self, client, auth_headers, mock_db):
        _, cur = mock_db()
        r = client.get(LIST_URL, headers=auth_headers)
        assert r.status_code == 400
        cur.execute.assert_not_called()

    # Rows present → JSON list of dicts.
    def test_returns_list_of_datasets(self, client, auth_headers, mock_db):
        mock_db(fetchall=[_row()], description=ROW_DESC)
        r = client.get(f'{LIST_URL}?owner_slug={OWNER}', headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json()[0]['dataset_id'] == DATASET_ID

    # No matching rows returns an empty list.
    def test_empty_result_returns_empty_list(self, client, auth_headers, mock_db):
        mock_db(fetchall=[], description=ROW_DESC)
        r = client.get(f'{LIST_URL}?owner_slug={OWNER}', headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json() == []

    # ?mode=X appends "AND mode = %s" and binds X.
    def test_mode_filter_appended(self, client, auth_headers, mock_db):
        _, cur = mock_db(description=ROW_DESC)
        client.get(f'{LIST_URL}?owner_slug={OWNER}&mode={MODE}', headers=auth_headers)
        sql, params = cur.execute.call_args.args
        assert 'AND mode = %s' in sql
        assert MODE in params

    # ?name=X appends "AND name = %s" and binds X.
    def test_name_filter_appended(self, client, auth_headers, mock_db):
        _, cur = mock_db(description=ROW_DESC)
        client.get(f'{LIST_URL}?owner_slug={OWNER}&name={NAME}', headers=auth_headers)
        sql, params = cur.execute.call_args.args
        assert 'AND name = %s' in sql
        assert NAME in params

    # Newest datasets come first.
    def test_orders_by_created_at_descending(self, client, auth_headers, mock_db):
        _, cur = mock_db(description=ROW_DESC)
        client.get(f'{LIST_URL}?owner_slug={OWNER}', headers=auth_headers)
        sql, _ = cur.execute.call_args.args
        assert 'ORDER BY created_at DESC' in sql

    # No page/per_page → LIMIT 100 OFFSET 0 (defaults; per_page defaults to 100 here).
    def test_applies_default_pagination(self, client, auth_headers, mock_db):
        _, cur = mock_db(description=ROW_DESC)
        client.get(f'{LIST_URL}?owner_slug={OWNER}', headers=auth_headers)
        sql, params = cur.execute.call_args.args
        assert 'LIMIT %s OFFSET %s' in sql
        assert params[-2:] == (100, 0)

    # page=3, per_page=10 → LIMIT 10 OFFSET 20.
    def test_applies_custom_pagination(self, client, auth_headers, mock_db):
        _, cur = mock_db(description=ROW_DESC)
        client.get(f'{LIST_URL}?owner_slug={OWNER}&page=3&per_page=10', headers=auth_headers)
        _, params = cur.execute.call_args.args
        assert params[-2:] == (10, 20)


class TestDatasetItemGet:
    # Existing dataset returns 200 with the row.
    def test_found_returns_200(self, client, auth_headers, mock_db):
        mock_db(fetchone=_row(), description=ROW_DESC)
        r = client.get(ITEM_URL.format(DATASET_ID), headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json()['dataset_id'] == DATASET_ID

    # Missing dataset returns 404.
    def test_not_found_returns_404(self, client, auth_headers, mock_db):
        mock_db(fetchone=None)
        r = client.get(ITEM_URL.format(DATASET_ID), headers=auth_headers)
        assert r.status_code == 404


class TestArchivePost:
    def _multipart(self, filename='dataset.tar.gz', **form):
        return {'file': (io.BytesIO(b'archive-blob'), filename), **form}

    # No file field returns 400 before touching DB or MinIO.
    def test_no_file_returns_400(self, client, auth_headers, mock_db, mock_storage):
        _, cur = mock_db()
        r = client.post(ARCHIVE_URL.format(DATASET_ID), headers=auth_headers, data={})
        assert r.status_code == 400
        cur.execute.assert_not_called()
        mock_storage['upload'].assert_not_called()

    # Empty filename triggers the same 400 branch.
    def test_empty_filename_returns_400(self, client, auth_headers, mock_db, mock_storage):
        _, cur = mock_db()
        r = client.post(
            ARCHIVE_URL.format(DATASET_ID), headers=auth_headers,
            data={'file': (io.BytesIO(b'x'), '')},
        )
        assert r.status_code == 400
        cur.execute.assert_not_called()
        mock_storage['upload'].assert_not_called()

    # Dataset doesn't exist → 404 BEFORE the file is uploaded to MinIO.
    def test_dataset_not_found_returns_404_before_upload(self, client, auth_headers, mock_db, mock_storage):
        mock_db(fetchone=None)  # _fetch_dataset returns None
        r = client.post(
            ARCHIVE_URL.format(DATASET_ID), headers=auth_headers, data=self._multipart(),
        )
        assert r.status_code == 404
        mock_storage['upload'].assert_not_called()

    # Happy path: DB check passes → upload → UPDATE → 200 with archive metadata.
    def test_happy_path_returns_200(self, client, auth_headers, mock_db, mock_storage):
        # First fetchone: initial _fetch_dataset returns the row (via fetch_one_dict).
        # Second fetchone: UPDATE ... RETURNING dataset_id returns the id.
        mock_db(fetchone_seq=[_row(), (DATASET_ID,)], description=ROW_DESC)
        r = client.post(
            ARCHIVE_URL.format(DATASET_ID), headers=auth_headers, data=self._multipart(),
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body['message'] == 'archive stored'
        assert body['object_key'] == f'{DATASET_ID}/dataset.tar.gz'
        assert body['size_bytes'] == 1234
        assert body['archive_url'].endswith(f'/storage/amalthai-datasets/{DATASET_ID}/dataset.tar.gz')

    # upload_filestorage receives the amalthai-datasets bucket + <id>/<filename> key.
    def test_upload_receives_bucket_and_object_key(self, client, auth_headers, mock_db, mock_storage):
        mock_db(fetchone_seq=[_row(), (DATASET_ID,)], description=ROW_DESC)
        client.post(
            ARCHIVE_URL.format(DATASET_ID), headers=auth_headers, data=self._multipart(),
        )
        args = mock_storage['upload'].call_args.args
        assert args[0] == 'amalthai-datasets'
        assert args[1] == f'{DATASET_ID}/dataset.tar.gz'

    # UPDATE persists object_key, archive_url, size_bytes and flips status to 'ready'.
    def test_update_sets_archive_fields_and_status(self, client, auth_headers, mock_db, mock_storage):
        _, cur = mock_db(fetchone_seq=[_row(), (DATASET_ID,)], description=ROW_DESC)
        client.post(
            ARCHIVE_URL.format(DATASET_ID), headers=auth_headers, data=self._multipart(),
        )
        # The last execute call is the UPDATE
        update_sql, update_params = cur.execute.call_args.args
        assert update_sql.startswith('UPDATE amalthai_datasets')
        assert "status='ready'" in update_sql
        assert f'{DATASET_ID}/dataset.tar.gz' in update_params
        assert 1234 in update_params

    # A content_hash form field is bound via COALESCE (keeps existing hash if omitted).
    def test_content_hash_from_form_bound(self, client, auth_headers, mock_db, mock_storage):
        _, cur = mock_db(fetchone_seq=[_row(), (DATASET_ID,)], description=ROW_DESC)
        client.post(
            ARCHIVE_URL.format(DATASET_ID), headers=auth_headers,
            data=self._multipart(content_hash='abc123'),
        )
        _, update_params = cur.execute.call_args.args
        assert 'abc123' in update_params

    # UPDATE ... RETURNING nothing → 404 (dataset was deleted between our two DB blocks).
    def test_update_returning_none_returns_404(self, client, auth_headers, mock_db, mock_storage):
        mock_db(fetchone_seq=[_row(), None], description=ROW_DESC)
        r = client.post(
            ARCHIVE_URL.format(DATASET_ID), headers=auth_headers, data=self._multipart(),
        )
        assert r.status_code == 404

    # An unexpected exception surfaces as 500 with an error body.
    def test_db_error_returns_500(self, client, auth_headers, mocker):
        mocker.patch(
            'resources.amalthai_dataset.get_db_connection',
            side_effect=Exception('db down'),
        )
        r = client.post(
            ARCHIVE_URL.format(DATASET_ID), headers=auth_headers,
            data={'file': (io.BytesIO(b'x'), 'x.tar.gz')},
        )
        assert r.status_code == 500


class TestArchiveGet:
    # No dataset row → 404.
    def test_dataset_not_found_returns_404(self, client, auth_headers, mock_db, mock_storage):
        mock_db(fetchone=None)
        r = client.get(ARCHIVE_URL.format(DATASET_ID), headers=auth_headers)
        assert r.status_code == 404
        mock_storage['stream'].assert_not_called()

    # Dataset row exists but object_key is None → 404 (registered but no archive uploaded).
    def test_no_object_key_returns_404(self, client, auth_headers, mock_db, mock_storage):
        mock_db(fetchone=_row(object_key=None), description=ROW_DESC)
        r = client.get(ARCHIVE_URL.format(DATASET_ID), headers=auth_headers)
        assert r.status_code == 404
        mock_storage['stream'].assert_not_called()

    # Happy path streams the MinIO object; stream_object receives bucket + key + download_name.
    def test_happy_path_streams_object(self, client, auth_headers, mocker, mock_db, mock_storage):
        object_key = f'{DATASET_ID}/dataset.tar.gz'
        mock_db(fetchone=_row(object_key=object_key), description=ROW_DESC)
        # Make stream_object return a Flask Response so Flask's test client can iterate it.
        from flask import Response
        mock_storage['stream'].return_value = Response(b'streamed-bytes', mimetype='application/octet-stream')

        r = client.get(ARCHIVE_URL.format(DATASET_ID), headers=auth_headers)

        assert r.status_code == 200
        assert r.data == b'streamed-bytes'
        args, kwargs = mock_storage['stream'].call_args
        assert args[0] == 'amalthai-datasets'
        assert args[1] == object_key
        assert kwargs['download_name'] == 'dataset.tar.gz'

    # A MinIO S3Error while streaming (object disappeared) surfaces as 404.
    def test_s3_error_returns_404(self, client, auth_headers, mocker, mock_db, mock_storage):
        from minio.error import S3Error
        object_key = f'{DATASET_ID}/dataset.tar.gz'
        mock_db(fetchone=_row(object_key=object_key), description=ROW_DESC)
        # S3Error signature varies across minio versions; construct one via bare Exception subclass.
        mock_storage['stream'].side_effect = S3Error(
            code='NoSuchKey', message='not found', resource='r',
            request_id='rid', host_id='hid', response=None, bucket_name='amalthai-datasets',
            object_name=object_key,
        )
        r = client.get(ARCHIVE_URL.format(DATASET_ID), headers=auth_headers)
        assert r.status_code == 404
