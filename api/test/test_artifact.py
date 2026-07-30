import io
import json
import uuid

import pytest


RESOURCE_MODULE = 'resources.artifact'

LIST_URL = '/artifacts'
ITEM_URL = '/artifacts/{}'
AGGREGATE_URL = '/artefacts/{}'  # note: 'artefacts' (British spelling), a distinct endpoint

FAKE_UUID = uuid.UUID('12345678-1234-5678-1234-567812345678')
FAKE_UUID_STR = str(FAKE_UUID)


# Patches MinIO put_object + both Kafka helpers to happy defaults. Also patches
# uuid.uuid4 so the response artifact_id is deterministic across assertions.
@pytest.fixture()
def upload_pipeline(mocker):
    put = mocker.patch('resources.artifact.minio_client.put_object')
    avro = mocker.patch('resources.artifact.send_avro_message', return_value=True)
    simple = mocker.patch('resources.artifact.send_simple_message', return_value=True)
    mocker.patch('resources.artifact.uuid.uuid4', return_value=FAKE_UUID)
    return {'put': put, 'avro': avro, 'simple': simple}


class TestAuth:
    # POST /artifacts is rejected without an Authorization header.
    def test_post_missing_header_returns_401(self, client):
        r = client.post(LIST_URL, data={'file': (io.BytesIO(b'x'), 'x.jpg')}, content_type='multipart/form-data')
        assert r.status_code == 401

    # GET /artifacts is rejected without an Authorization header.
    def test_get_list_missing_header_returns_401(self, client):
        r = client.get(LIST_URL)
        assert r.status_code == 401

    # GET /artifacts/<id> is rejected without an Authorization header.
    def test_get_item_missing_header_returns_401(self, client):
        r = client.get(ITEM_URL.format(FAKE_UUID_STR))
        assert r.status_code == 401

    # GET /artefacts/<id> (aggregate) is rejected without an Authorization header.
    def test_get_aggregate_missing_header_returns_401(self, client):
        r = client.get(AGGREGATE_URL.format(FAKE_UUID_STR))
        assert r.status_code == 401


class TestPost:
    # No 'file' field in the multipart form returns 400 without touching MinIO / Kafka.
    def test_no_file_returns_400(self, client, auth_headers, upload_pipeline):
        r = client.post(LIST_URL, headers=auth_headers, data={}, content_type='multipart/form-data')
        assert r.status_code == 400
        upload_pipeline['put'].assert_not_called()
        upload_pipeline['avro'].assert_not_called()

    # A file entry with an empty filename triggers the same "no file" 400 branch.
    def test_empty_filename_returns_400(self, client, auth_headers, upload_pipeline):
        r = client.post(
            LIST_URL, headers=auth_headers,
            data={'file': (io.BytesIO(b'x'), '')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 400
        upload_pipeline['put'].assert_not_called()

    # Sending multiple files without a metadata_map is ambiguous — 400.
    def test_multiple_files_without_metadata_map_returns_400(self, client, auth_headers, upload_pipeline):
        r = client.post(
            LIST_URL, headers=auth_headers,
            data={'file': [(io.BytesIO(b'a'), 'a.jpg'), (io.BytesIO(b'b'), 'b.jpg')]},
            content_type='multipart/form-data',
        )
        assert r.status_code == 400
        assert 'metadata_map' in r.get_json()['error']
        upload_pipeline['put'].assert_not_called()

    # metadata_map that isn't valid JSON returns 400.
    def test_invalid_metadata_map_json_returns_400(self, client, auth_headers, upload_pipeline):
        r = client.post(
            LIST_URL, headers=auth_headers,
            data={'file': (io.BytesIO(b'x'), 'x.jpg'), 'metadata_map': '{not: json'},
            content_type='multipart/form-data',
        )
        assert r.status_code == 400
        assert 'metadata_map' in r.get_json()['error']

    # Single-file happy path: 201 with an uploaded_files entry echoing the metadata.
    def test_single_file_happy_path_returns_201(self, client, auth_headers, upload_pipeline):
        r = client.post(
            LIST_URL, headers=auth_headers,
            data={'file': (io.BytesIO(b'content'), 'test.jpg'), 'title': 'My Title', 'drone_id': 'd-1'},
            content_type='multipart/form-data',
        )
        assert r.status_code == 201
        body = r.get_json()
        assert len(body['uploaded_files']) == 1
        item = body['uploaded_files'][0]
        assert item['artifact_id'] == FAKE_UUID_STR
        assert item['filename'] == 'test.jpg'
        assert item['title'] == 'My Title'
        assert item['drone_id'] == 'd-1'
        assert 'location' in item and 'public_url' in item

    # MinIO put_object is called with the artifacts bucket + <uuid>/<filename> key.
    def test_minio_put_receives_bucket_and_key(self, client, auth_headers, upload_pipeline):
        client.post(
            LIST_URL, headers=auth_headers,
            data={'file': (io.BytesIO(b'content'), 'test.jpg')},
            content_type='multipart/form-data',
        )
        args, _ = upload_pipeline['put'].call_args
        assert args[0] == 'artifacts'
        assert args[1] == f'{FAKE_UUID_STR}/test.jpg'

    # send_avro_message is called with the artifacts topic and the full record.
    def test_avro_called_with_artifacts_topic_and_record(self, client, auth_headers, upload_pipeline):
        client.post(
            LIST_URL, headers=auth_headers,
            data={'file': (io.BytesIO(b'content'), 'test.jpg'), 'drone_id': 'd-9'},
            content_type='multipart/form-data',
        )
        topic, key, value, _schema = upload_pipeline['avro'].call_args.args
        assert topic == 'artifacts'
        assert key == FAKE_UUID_STR
        assert value['artifact_id'] == FAKE_UUID_STR
        assert value['filename'] == 'test.jpg'
        assert value['drone_id'] == 'd-9'

    # Optional form fields fall back to hard-coded defaults when absent.
    def test_default_metadata_applied(self, client, auth_headers, upload_pipeline):
        client.post(
            LIST_URL, headers=auth_headers,
            data={'file': (io.BytesIO(b'x'), 'x.jpg')},
            content_type='multipart/form-data',
        )
        _, _, value, _ = upload_pipeline['avro'].call_args.args
        assert value['title'] == 'x.jpg'  # defaults to filename
        assert value['uploaded_by'] == 'user123'
        assert value['drone_id'] == 'unknown_drone'

    # Batch upload with metadata_map processes each file with its own metadata.
    def test_batch_upload_happy_path_returns_201(self, client, auth_headers, upload_pipeline):
        r = client.post(
            LIST_URL, headers=auth_headers,
            data={
                'file': [
                    (io.BytesIO(b'a'), 'a.jpg'),
                    (io.BytesIO(b'b'), 'b.jpg'),
                ],
                'metadata_map': json.dumps({
                    'a.jpg': {'title': 'A', 'drone_id': 'd-a'},
                    'b.jpg': {'title': 'B', 'drone_id': 'd-b'},
                }),
            },
            content_type='multipart/form-data',
        )
        assert r.status_code == 201
        assert len(r.get_json()['uploaded_files']) == 2
        assert upload_pipeline['put'].call_count == 2

    # If every upload raises (e.g. Kafka avro fails), the endpoint returns 500.
    def test_all_uploads_fail_returns_500(self, client, auth_headers, upload_pipeline):
        upload_pipeline['avro'].return_value = False  # raises inside upload_single_file
        r = client.post(
            LIST_URL, headers=auth_headers,
            data={'file': (io.BytesIO(b'x'), 'x.jpg')},
            content_type='multipart/form-data',
        )
        assert r.status_code == 500
        assert r.get_json() == {'error': 'File upload failed'}

    # In a batch, a partial failure returns 201 with only the successful entries.
    def test_batch_partial_failure_returns_201_with_successful_subset(self, client, auth_headers, upload_pipeline):
        upload_pipeline['put'].side_effect = [None, Exception('minio boom')]
        r = client.post(
            LIST_URL, headers=auth_headers,
            data={
                'file': [
                    (io.BytesIO(b'a'), 'a.jpg'),
                    (io.BytesIO(b'b'), 'b.jpg'),
                ],
                'metadata_map': json.dumps({'a.jpg': {}, 'b.jpg': {}}),
            },
            content_type='multipart/form-data',
        )
        assert r.status_code == 201
        assert len(r.get_json()['uploaded_files']) == 1


class TestGetList:
    def _description(self):
        return [('artifact_id',), ('filename',), ('drone_id',), ('title',)]

    def _row(self, artifact_id=FAKE_UUID_STR, filename='a.jpg', drone_id='d-1', title='T'):
        return (artifact_id, filename, drone_id, title)

    # No filters + rows present → JSON list of dicts.
    def test_returns_list_of_artifacts(self, client, auth_headers, mock_db):
        mock_db(fetchall=[self._row()], description=self._description())
        r = client.get(LIST_URL, headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json()[0]['artifact_id'] == FAKE_UUID_STR

    # ?drone_id=X appends "drone_id = %s" and binds X.
    def test_drone_id_filter_appended(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchall=[], description=self._description())
        client.get(f'{LIST_URL}?drone_id=d-1', headers=auth_headers)
        sql, params = cur.execute.call_args.args
        assert 'drone_id = %s' in sql
        assert 'd-1' in params

    # Multiple filters are joined with AND.
    def test_multiple_filters_joined_with_and(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchall=[], description=self._description())
        client.get(f'{LIST_URL}?drone_id=d-1&title=T&uploaded_by=me', headers=auth_headers)
        sql, _ = cur.execute.call_args.args
        assert sql.count(' AND ') == 2

    # Non-whitelisted filter keys are silently ignored (never reach the SQL).
    def test_unknown_filter_key_is_ignored(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchall=[], description=self._description())
        client.get(f'{LIST_URL}?malicious_key=x', headers=auth_headers)
        sql, params = cur.execute.call_args.args
        assert 'malicious_key' not in sql
        assert 'x' not in params

    # ?fields=filename,drone_id projects the SELECT clause.
    def test_fields_projection_narrows_select(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchall=[], description=self._description())
        client.get(f'{LIST_URL}?fields=filename,drone_id', headers=auth_headers)
        sql, _ = cur.execute.call_args.args
        assert sql.startswith('SELECT filename, drone_id FROM artifacts')

    # Unsafe field names (SQL fragments) are stripped from the projection.
    def test_fields_projection_filters_unsafe_names(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchall=[], description=self._description())
        client.get(f'{LIST_URL}?fields=filename;DROP TABLE artifacts', headers=auth_headers)
        sql, _ = cur.execute.call_args.args
        assert 'DROP' not in sql

    # A DB connection failure surfaces as 500 with an error body.
    def test_db_error_returns_500(self, client, auth_headers, mocker):
        mocker.patch('resources.artifact.get_db_connection', side_effect=Exception('db down'))
        r = client.get(LIST_URL, headers=auth_headers)
        assert r.status_code == 500


class TestGetItem:
    def _description(self):
        return [('artifact_id',), ('filename',)]

    # Existing artifact returns 200 with the row as a dict.
    def test_found_returns_200(self, client, auth_headers, mock_db):
        mock_db(fetchone=(FAKE_UUID_STR, 'a.jpg'), description=self._description())
        r = client.get(ITEM_URL.format(FAKE_UUID_STR), headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json() == {'artifact_id': FAKE_UUID_STR, 'filename': 'a.jpg'}

    # Missing artifact returns 404.
    def test_not_found_returns_404(self, client, auth_headers, mock_db):
        mock_db(fetchone=None)
        r = client.get(ITEM_URL.format(FAKE_UUID_STR), headers=auth_headers)
        assert r.status_code == 404

    # A DB connection failure surfaces as 500.
    def test_db_error_returns_500(self, client, auth_headers, mocker):
        mocker.patch('resources.artifact.get_db_connection', side_effect=Exception('down'))
        r = client.get(ITEM_URL.format(FAKE_UUID_STR), headers=auth_headers)
        assert r.status_code == 500


class TestGetAggregate:
    # Missing artifact returns 404 without querying the related tables.
    def test_artifact_not_found_returns_404(self, client, auth_headers, mock_db):
        _, cur = mock_db(fetchone=None)
        r = client.get(AGGREGATE_URL.format(FAKE_UUID_STR), headers=auth_headers)
        assert r.status_code == 404
        # Only the initial artifact SELECT should have run
        assert cur.execute.call_count == 1

    # Found artifact + every related table present → combined payload with rows.
    def test_found_returns_artifact_with_related_rows(self, client, auth_headers, mocker):
        conn = mocker.MagicMock(name='pg_conn')
        cur = mocker.MagicMock(name='pg_cursor')
        conn.cursor.return_value = cur

        artifact_row = (FAKE_UUID_STR, 'a.jpg')
        related_row = (1, FAKE_UUID_STR)

        # Sequence of fetchone calls: 1 for artifact + 5 for to_regclass (all non-null)
        cur.fetchone.side_effect = [artifact_row] + [('exists',)] * 5
        # Sequence of fetchall calls: one per related table
        cur.fetchall.side_effect = [[related_row]] * 5
        # description used by _rows_to_dicts for artifact + each related table
        type(cur).description = mocker.PropertyMock(return_value=[('id',), ('artifact_id',)])

        mocker.patch('resources.artifact.get_db_connection', return_value=conn)

        r = client.get(AGGREGATE_URL.format(FAKE_UUID_STR), headers=auth_headers)
        assert r.status_code == 200
        body = r.get_json()
        assert 'artifact' in body
        for key in ('sensor_readings', 'robot_images', 'reconstructions', 'annotations', 'nefele_jobs'):
            assert key in body
            assert len(body[key]) == 1

    # Related tables missing (to_regclass returns NULL) → empty list for those keys.
    def test_missing_related_tables_return_empty_lists(self, client, auth_headers, mocker):
        conn = mocker.MagicMock(name='pg_conn')
        cur = mocker.MagicMock(name='pg_cursor')
        conn.cursor.return_value = cur

        artifact_row = (FAKE_UUID_STR, 'a.jpg')
        # artifact SELECT returns the row; each to_regclass returns (None,)
        cur.fetchone.side_effect = [artifact_row] + [(None,)] * 5
        type(cur).description = mocker.PropertyMock(return_value=[('artifact_id',), ('filename',)])

        mocker.patch('resources.artifact.get_db_connection', return_value=conn)

        r = client.get(AGGREGATE_URL.format(FAKE_UUID_STR), headers=auth_headers)
        assert r.status_code == 200
        body = r.get_json()
        for key in ('sensor_readings', 'robot_images', 'reconstructions', 'annotations', 'nefele_jobs'):
            assert body[key] == []

    # A DB error before the artifact SELECT completes surfaces as 500.
    def test_db_error_returns_500(self, client, auth_headers, mocker):
        mocker.patch('resources.artifact.get_db_connection', side_effect=Exception('down'))
        r = client.get(AGGREGATE_URL.format(FAKE_UUID_STR), headers=auth_headers)
        assert r.status_code == 500
