import io
import json
import uuid
from datetime import datetime, timezone

import pytest


URL = '/robot-images'
SCAN_ID = 'scan-1'
ARTIFACT_ID = 'art-123'
FILENAME = 'photo.png'
TIMESTAMP = '2026-01-01T00:00:00+00:00'
IMAGE_BYTES = b'fake-image-bytes'
ROBOT_POSE = 'x=1,y=2'
BUCKET = 'robot-images'  # MINIO_ROBOT_BUCKET


# Builds a multipart body carrying one dummy image plus a metadata_map keyed by
# filename, which is the shape the resource expects.
def _multipart(metadata_map=None, filename=FILENAME, **form):
    data = {'file': (io.BytesIO(IMAGE_BYTES), filename), **form}
    if metadata_map is not None:
        data['metadata_map'] = json.dumps(metadata_map)
    return data


# Builds a mock (conn, cursor) pair that supports BOTH the direct usage GET does
# (`conn = get_db_connection(); cur = conn.cursor()`) and the context-manager
# usage the upload helper does (`with get_db_connection() as conn, conn.cursor() as cur`).
# Rows, description and rowcount are configurable per test.
@pytest.fixture()
def mock_db(mocker):
    def _factory(fetchall=None, description=None, rowcount=1):
        conn = mocker.MagicMock(name='pg_conn')
        conn.__enter__.return_value = conn
        conn.__exit__.return_value = False

        cur = mocker.MagicMock(name='pg_cursor')
        cur.__enter__.return_value = cur
        cur.__exit__.return_value = False
        cur.fetchall.return_value = fetchall if fetchall is not None else []
        cur.description = description
        cur.rowcount = rowcount

        conn.cursor.return_value = cur

        mocker.patch('resources.robot.get_db_connection', return_value=conn)
        return conn, cur
    return _factory


# Wires the whole POST pipeline for a successful upload: MinIO accepts the object,
# the DB insert affects one row and both Kafka publishes succeed. Individual tests
# override any single step to force its failure branch.
@pytest.fixture()
def happy_pipeline(mocker, mock_db):
    conn, cur = mock_db(rowcount=1)
    return {
        'conn': conn,
        'cur': cur,
        'minio': mocker.patch('resources.robot.minio_client'),
        'avro': mocker.patch('resources.robot.send_avro_message', return_value=True),
        'simple': mocker.patch('resources.robot.send_simple_message', return_value=True),
    }


class TestAuth:
    # GET without an Authorization header is rejected before touching the DB.
    def test_get_missing_header_returns_401(self, client):
        r = client.get(URL)
        assert r.status_code == 401

    # A bearer token that doesn't match the configured key is rejected.
    def test_get_wrong_bearer_returns_401(self, client):
        r = client.get(URL, headers={'Authorization': 'Bearer wrong'})
        assert r.status_code == 401

    # POST without an Authorization header is rejected before the upload runs.
    def test_post_missing_header_returns_401(self, client):
        r = client.post(URL, data=_multipart({}))
        assert r.status_code == 401


class TestGet:
    def _description(self):
        # psycopg2's cur.description is a list of Column-like objects whose first
        # field is the column name — a plain (name,) tuple satisfies desc[0].
        return [('image_id',), ('scan_id',), ('filename',), ('timestamp',)]

    def _row(self, timestamp=TIMESTAMP):
        # Emulates a psycopg2 tuple row matching the columns above.
        return ('img-1', SCAN_ID, FILENAME, timestamp)

    # Rows are zipped against cur.description and returned as a JSON list.
    def test_returns_rows_as_json_list(self, client, auth_headers, mock_db):
        mock_db(fetchall=[self._row()], description=self._description())

        r = client.get(URL, headers=auth_headers)

        assert r.status_code == 200
        assert r.get_json() == [{
            'image_id': 'img-1',
            'scan_id': SCAN_ID,
            'filename': FILENAME,
            'timestamp': TIMESTAMP,
        }]

    # No rows is not an error for this resource — it returns an empty list with a 200.
    def test_empty_result_returns_empty_list(self, client, auth_headers, mock_db):
        mock_db(fetchall=[], description=self._description())

        r = client.get(URL, headers=auth_headers)

        assert r.status_code == 200
        assert r.get_json() == []

    # datetime values coming out of psycopg2 are serialized with .isoformat().
    def test_serializes_datetime_columns(self, client, auth_headers, mock_db):
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mock_db(fetchall=[self._row(timestamp=stamp)], description=self._description())

        r = client.get(URL, headers=auth_headers)

        assert r.get_json()[0]['timestamp'] == stamp.isoformat()

    # ?scan_id=... appends "AND scan_id = %s" to the SQL and binds the value.
    def test_filters_by_scan_id(self, client, auth_headers, mock_db):
        _, cur = mock_db()
        client.get(f'{URL}?scan_id={SCAN_ID}', headers=auth_headers)

        sql, params = cur.execute.call_args.args
        assert 'AND scan_id = %s' in sql
        assert SCAN_ID in params

    # Images within a scan come back in capture order, oldest first.
    def test_orders_by_timestamp_ascending(self, client, auth_headers, mock_db):
        _, cur = mock_db()
        client.get(URL, headers=auth_headers)

        sql, _params = cur.execute.call_args.args
        assert 'ORDER BY timestamp ASC' in sql

    # No page/per_page params → LIMIT 50 OFFSET 0 (defaults).
    def test_applies_default_pagination(self, client, auth_headers, mock_db):
        _, cur = mock_db()
        client.get(URL, headers=auth_headers)

        sql, params = cur.execute.call_args.args
        assert 'LIMIT %s OFFSET %s' in sql
        assert params[-2:] == (50, 0)

    # page=3, per_page=10 → LIMIT 10 OFFSET 20.
    def test_applies_custom_pagination(self, client, auth_headers, mock_db):
        _, cur = mock_db()
        client.get(f'{URL}?page=3&per_page=10', headers=auth_headers)

        _sql, params = cur.execute.call_args.args
        assert params[-2:] == (10, 20)

    # Non-integer pagination params are rejected before the query is built.
    @pytest.mark.parametrize('query', ['page=abc', 'per_page=abc'])
    def test_non_integer_pagination_returns_400(self, client, auth_headers, mock_db, query):
        _, cur = mock_db()

        r = client.get(f'{URL}?{query}', headers=auth_headers)

        assert r.status_code == 400
        assert r.get_json() == {'error': 'Page and per_page must be integers'}
        cur.execute.assert_not_called()

    # The cursor and connection are both released on the success path.
    def test_db_connection_closed(self, client, auth_headers, mock_db):
        conn, cur = mock_db(description=self._description())
        client.get(URL, headers=auth_headers)

        cur.close.assert_called_once()
        conn.close.assert_called_once()

    # A DB connection failure is caught by the outer try and surfaces as a 500.
    def test_db_error_returns_500(self, client, auth_headers, mocker):
        mocker.patch(
            'resources.robot.get_db_connection',
            side_effect=Exception('db down'),
        )
        r = client.get(URL, headers=auth_headers)

        assert r.status_code == 500
        assert 'error' in r.get_json()


class TestPostValidation:
    # Both "no file field at all" and "file field with an empty filename" are 400s,
    # and neither reaches MinIO.
    @pytest.mark.parametrize('data', [
        {'metadata_map': '{}'},                                   # no file part
        {'file': (io.BytesIO(b''), ''), 'metadata_map': '{}'},    # empty filename
    ])
    def test_missing_file_returns_400(self, client, auth_headers, happy_pipeline, data):
        r = client.post(URL, headers=auth_headers, data=data)

        assert r.status_code == 400
        assert r.get_json() == {'error': 'No file(s) provided.'}
        happy_pipeline['minio'].put_object.assert_not_called()

    # metadata_map is mandatory even when a file is present.
    def test_missing_metadata_map_returns_400(self, client, auth_headers, happy_pipeline):
        r = client.post(URL, headers=auth_headers, data=_multipart())

        assert r.status_code == 400
        assert r.get_json() == {'error': "No 'metadata_map' provided."}
        happy_pipeline['minio'].put_object.assert_not_called()

    # A malformed metadata_map is rejected before anything is uploaded.
    def test_invalid_metadata_json_returns_400(self, client, auth_headers, happy_pipeline):
        data = _multipart() | {'metadata_map': '{not json'}

        r = client.post(URL, headers=auth_headers, data=data)

        assert r.status_code == 400
        assert r.get_json() == {'error': "Invalid JSON in 'metadata_map' field."}
        happy_pipeline['minio'].put_object.assert_not_called()


class TestPostUpload:
    # A valid upload returns 201 with the scan id and one entry per stored file.
    def test_happy_path_returns_201(self, client, auth_headers, happy_pipeline):
        data = _multipart({FILENAME: {'robot_pose': ROBOT_POSE}}, scan_id=SCAN_ID)

        r = client.post(URL, headers=auth_headers, data=data)

        assert r.status_code == 201
        body = r.get_json()
        assert body['message'] == 'Successfully processed 1 file(s).'
        assert body['scan_id'] == SCAN_ID
        [uploaded] = body['uploaded_files']
        assert uploaded['filename'] == FILENAME
        assert uploaded['robot_pose'] == ROBOT_POSE

    # Objects are grouped into a per-scan folder and pushed with the file's bytes and length.
    def test_uploads_object_under_scan_id_folder(self, client, auth_headers, happy_pipeline):
        data = _multipart({}, scan_id=SCAN_ID)

        client.post(URL, headers=auth_headers, data=data)

        args, kwargs = happy_pipeline['minio'].put_object.call_args
        bucket, object_name, stream, length = args
        assert bucket == BUCKET
        assert object_name == f'{SCAN_ID}/{FILENAME}'
        assert stream.read() == IMAGE_BYTES
        assert length == len(IMAGE_BYTES)
        assert kwargs['content_type'] == 'image/png'

    # With no scan_id in the form the resource generates a UUID, and that same id is
    # used for the object path so the response and storage layout stay consistent.
    def test_generates_scan_id_when_absent(self, client, auth_headers, happy_pipeline):
        r = client.post(URL, headers=auth_headers, data=_multipart({}))

        generated = r.get_json()['scan_id']
        assert uuid.UUID(generated)  # raises if it isn't a well-formed UUID
        _bucket, object_name, *_ = happy_pipeline['minio'].put_object.call_args.args
        assert object_name == f'{generated}/{FILENAME}'

    # The stored record carries a public URL pointing at the file proxy route.
    def test_record_public_url_points_at_storage_route(self, client, auth_headers, happy_pipeline):
        data = _multipart({}, scan_id=SCAN_ID)

        r = client.post(URL, headers=auth_headers, data=data)

        [uploaded] = r.get_json()['uploaded_files']
        assert uploaded['location'] == f's3://{BUCKET}/{SCAN_ID}/{FILENAME}'
        assert uploaded['public_url'].endswith(f'/storage/{BUCKET}/{SCAN_ID}/{FILENAME}')

    # artifact_id can come from the form and applies to every file in the batch.
    def test_artifact_id_from_form_is_applied(self, client, auth_headers, happy_pipeline):
        data = _multipart({}, scan_id=SCAN_ID, artifact_id=ARTIFACT_ID)

        r = client.post(URL, headers=auth_headers, data=data)

        [uploaded] = r.get_json()['uploaded_files']
        assert uploaded['artifact_id'] == ARTIFACT_ID

    # A per-file artifact_id in metadata_map wins over the batch-level form value.
    def test_artifact_id_in_metadata_overrides_form(self, client, auth_headers, happy_pipeline):
        data = _multipart(
            {FILENAME: {'artifact_id': 'art-from-metadata'}},
            scan_id=SCAN_ID,
            artifact_id=ARTIFACT_ID,
        )

        r = client.post(URL, headers=auth_headers, data=data)

        [uploaded] = r.get_json()['uploaded_files']
        assert uploaded['artifact_id'] == 'art-from-metadata'

    # Metadata is looked up by filename, so a map keyed for a different file leaves
    # this one's pose unset rather than borrowing the wrong metadata.
    def test_metadata_for_other_filename_is_not_applied(self, client, auth_headers, happy_pipeline):
        data = _multipart({'other.png': {'robot_pose': ROBOT_POSE}}, scan_id=SCAN_ID)

        r = client.post(URL, headers=auth_headers, data=data)

        [uploaded] = r.get_json()['uploaded_files']
        assert uploaded['robot_pose'] is None

    # The INSERT names every key of the record and binds one placeholder per key.
    def test_insert_covers_every_record_key(self, client, auth_headers, happy_pipeline):
        data = _multipart({}, scan_id=SCAN_ID)

        r = client.post(URL, headers=auth_headers, data=data)

        [uploaded] = r.get_json()['uploaded_files']
        sql, params = happy_pipeline['cur'].execute.call_args.args
        assert sql.startswith('INSERT INTO robot_images (')
        assert len(params) == len(uploaded)
        for key in uploaded:
            assert key in sql

    # Every file in a multi-file request is uploaded under the same scan id.
    def test_multiple_files_are_all_uploaded(self, client, auth_headers, happy_pipeline):
        data = {
            'file': [
                (io.BytesIO(IMAGE_BYTES), 'a.png'),
                (io.BytesIO(IMAGE_BYTES), 'b.png'),
            ],
            'metadata_map': json.dumps({}),
            'scan_id': SCAN_ID,
        }

        r = client.post(URL, headers=auth_headers, data=data)

        assert r.status_code == 201
        body = r.get_json()
        assert body['message'] == 'Successfully processed 2 file(s).'
        assert [f['filename'] for f in body['uploaded_files']] == ['a.png', 'b.png']
        assert happy_pipeline['minio'].put_object.call_count == 2

    # After the row is stored, a notification is published to robot_image_uploaded.
    def test_notification_published_after_success(self, client, auth_headers, happy_pipeline):
        data = _multipart({}, scan_id=SCAN_ID)

        r = client.post(URL, headers=auth_headers, data=data)

        [uploaded] = r.get_json()['uploaded_files']
        topic, key, notification = happy_pipeline['simple'].call_args.args
        assert topic == 'robot_image_uploaded'
        assert key == uploaded['image_id']
        assert notification['scan_id'] == SCAN_ID

    # The record is published to the robot_images topic with the Avro schema.
    def test_avro_message_uses_robot_images_topic(self, client, auth_headers, happy_pipeline):
        data = _multipart({}, scan_id=SCAN_ID)

        client.post(URL, headers=auth_headers, data=data)

        topic, _key, published, schema = happy_pipeline['avro'].call_args.args
        assert topic == 'robot_images'
        assert published['scan_id'] == SCAN_ID
        assert 'RobotImage' in schema


class TestPostFailures:
    # A MinIO failure is caught per file; with nothing stored the request is a 500
    # and no metadata row is written.
    def test_minio_failure_returns_500(self, client, auth_headers, happy_pipeline):
        happy_pipeline['minio'].put_object.side_effect = Exception('minio down')

        r = client.post(URL, headers=auth_headers, data=_multipart({}))

        assert r.status_code == 500
        assert r.get_json() == {'error': 'File upload failed.'}
        happy_pipeline['cur'].execute.assert_not_called()

    # Avro validation failure aborts the file before the DB insert and before the
    # notification, so a rejected image leaves no trace.
    def test_avro_failure_returns_500_and_skips_db_and_notification(self, client, auth_headers, happy_pipeline):
        happy_pipeline['avro'].return_value = False

        r = client.post(URL, headers=auth_headers, data=_multipart({}))

        assert r.status_code == 500
        assert r.get_json() == {'error': 'File upload failed.'}
        happy_pipeline['cur'].execute.assert_not_called()
        happy_pipeline['simple'].assert_not_called()

    # An INSERT that affects no rows is treated as a failure and skips the notification.
    def test_insert_affecting_no_rows_returns_500(self, client, auth_headers, mock_db, mocker):
        _conn, cur = mock_db(rowcount=0)
        mocker.patch('resources.robot.minio_client')
        mocker.patch('resources.robot.send_avro_message', return_value=True)
        simple = mocker.patch('resources.robot.send_simple_message', return_value=True)

        r = client.post(URL, headers=auth_headers, data=_multipart({}))

        assert r.status_code == 500
        assert cur.execute.called
        simple.assert_not_called()

    # One bad file doesn't sink the batch: the request still returns 201 listing only
    # the files that made it through.
    def test_partial_failure_returns_201_with_successful_files_only(self, client, auth_headers, happy_pipeline):
        happy_pipeline['minio'].put_object.side_effect = [Exception('minio down'), None]
        data = {
            'file': [
                (io.BytesIO(IMAGE_BYTES), 'bad.png'),
                (io.BytesIO(IMAGE_BYTES), 'good.png'),
            ],
            'metadata_map': json.dumps({}),
            'scan_id': SCAN_ID,
        }

        r = client.post(URL, headers=auth_headers, data=data)

        assert r.status_code == 201
        body = r.get_json()
        assert body['message'] == 'Successfully processed 1 file(s).'
        assert [f['filename'] for f in body['uploaded_files']] == ['good.png']
