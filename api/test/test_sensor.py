from datetime import datetime, timezone

import pytest


RESOURCE_MODULE = 'resources.resource_base'

URL = '/sensor-readings'
SENSOR_ID = 'Teapot-Sensor-1'
TIMESTAMP = '2026-01-01T00:00:00+00:00'

VALID_READING = {'sensor_id': SENSOR_ID, 'temperature': 21.5, 'humidity': 40.0}


# Patches both Kafka publish helpers to succeed by default. Tests override
# individual return values to force the failure branches.
@pytest.fixture()
def mock_kafka(mocker):
    return {
        'avro': mocker.patch('resources.sensor.send_avro_message', return_value=True),
        'simple': mocker.patch('resources.sensor.send_simple_message', return_value=True),
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

    # The raw key without the "Bearer " prefix is not accepted.
    def test_get_missing_bearer_prefix_returns_401(self, client, api_key):
        r = client.get(URL, headers={'Authorization': api_key})
        assert r.status_code == 401

    # POST without an Authorization header is rejected before validation runs.
    def test_post_missing_header_returns_401(self, client):
        r = client.post(URL, json=VALID_READING)
        assert r.status_code == 401


class TestGet:
    def _description(self):
        # psycopg2's cur.description is a list of Column-like objects whose first
        # field is the column name — a plain (name,) tuple satisfies desc[0].
        return [('sensor_id',), ('timestamp',), ('temperature',), ('humidity',)]

    def _row(self, timestamp=TIMESTAMP):
        # Emulates a psycopg2 tuple row matching the columns above.
        return (SENSOR_ID, timestamp, 21.5, 40.0)

    # Rows are zipped against cur.description and returned as a JSON list.
    def test_returns_rows_as_json_list(self, client, auth_headers, mock_db):
        mock_db(fetchall=[self._row()], description=self._description())

        r = client.get(URL, headers=auth_headers)

        assert r.status_code == 200
        assert r.get_json() == [{
            'sensor_id': SENSOR_ID,
            'timestamp': TIMESTAMP,
            'temperature': 21.5,
            'humidity': 40.0,
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

    # ?sensor_id=... appends "AND sensor_id = %s" to the SQL and binds the value.
    def test_filters_by_sensor_id(self, client, auth_headers, mock_db):
        _, cur = mock_db()
        client.get(f'{URL}?sensor_id={SENSOR_ID}', headers=auth_headers)

        sql, params = cur.execute.call_args.args
        assert 'AND sensor_id = %s' in sql
        assert SENSOR_ID in params

    # start_date / end_date add one bounded clause each, in that order.
    def test_filters_by_date_range(self, client, auth_headers, mock_db):
        _, cur = mock_db()
        client.get(f'{URL}?start_date=2026-01-01&end_date=2026-02-01', headers=auth_headers)

        sql, params = cur.execute.call_args.args
        assert 'AND timestamp >= %s' in sql
        assert 'AND timestamp <= %s' in sql
        assert params[:2] == ('2026-01-01', '2026-02-01')

    # Readings come back newest first.
    def test_orders_by_timestamp_descending(self, client, auth_headers, mock_db):
        _, cur = mock_db()
        client.get(URL, headers=auth_headers)

        sql, _params = cur.execute.call_args.args
        assert 'ORDER BY timestamp DESC' in sql

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

        cur.__exit__.assert_called_once()
        conn.close.assert_called_once()

    # A DB connection failure is caught by the outer try and surfaces as a 500 with an error body.
    def test_db_error_returns_500(self, client, auth_headers, mocker):
        mocker.patch(
            'resources.resource_base.get_db_connection',
            side_effect=Exception('db down'),
        )
        r = client.get(URL, headers=auth_headers)

        assert r.status_code == 500
        assert 'error' in r.get_json()


class TestPost:
    def _valid_body(self, **overrides):
        body = dict(VALID_READING)
        body.update(overrides)
        return body

    # Empty JSON body triggers the "No data provided" 400 branch (empty dict is falsy).
    # Note: sending literally no body returns 415 from Flask before the resource runs.
    def test_empty_body_returns_400(self, client, auth_headers, mock_kafka):
        r = client.post(URL, headers=auth_headers, json={})

        assert r.status_code == 400
        assert r.get_json() == {'error': 'No data provided'}
        mock_kafka['avro'].assert_not_called()

    # sensor_id, temperature and humidity are all required — dropping any one is a 400.
    @pytest.mark.parametrize('missing', ['sensor_id', 'temperature', 'humidity'])
    def test_missing_required_field_returns_400(self, client, auth_headers, mock_kafka, missing):
        body = {k: v for k, v in VALID_READING.items() if k != missing}

        r = client.post(URL, headers=auth_headers, json=body)

        assert r.status_code == 400
        assert 'Missing required fields' in r.get_json()['error']
        mock_kafka['avro'].assert_not_called()

    # Keys outside the Avro schema are rejected *before* anything is published, so an
    # invalid reading can never reach the topic.
    def test_unknown_key_returns_400_without_publishing(self, client, auth_headers, mock_kafka):
        r = client.post(URL, headers=auth_headers, json=self._valid_body(nonsense='x'))

        assert r.status_code == 400
        assert r.get_json() == {'error': 'Not all given keys are valid'}
        mock_kafka['avro'].assert_not_called()
        mock_kafka['simple'].assert_not_called()

    # Every optional schema field is accepted alongside the required ones.
    @pytest.mark.parametrize('field, value', [
        ('uv_intensity', 3.2),
        ('luminosity', 800.0),
        ('atmospheric_pressure', 1013),
        ('elevation', 12.5),
        ('artifact_id', 'art-123'),
    ])
    def test_optional_schema_fields_are_accepted(self, client, auth_headers, mock_kafka, field, value):
        r = client.post(URL, headers=auth_headers, json=self._valid_body(**{field: value}))

        assert r.status_code == 201
        _topic, _key, published, _schema = mock_kafka['avro'].call_args.args
        assert published[field] == value

    # A valid reading returns 201 with the message key as its id.
    def test_happy_path_returns_201(self, client, auth_headers, mock_kafka):
        r = client.post(URL, headers=auth_headers, json=self._valid_body(timestamp=TIMESTAMP))

        assert r.status_code == 201
        assert r.get_json() == {
            'message': 'Reading received',
            'id': f'{SENSOR_ID}_{TIMESTAMP}',
        }

    # A caller-supplied timestamp is used verbatim rather than being regenerated.
    def test_uses_provided_timestamp(self, client, auth_headers, mock_kafka):
        client.post(URL, headers=auth_headers, json=self._valid_body(timestamp=TIMESTAMP))

        _topic, key, published, _schema = mock_kafka['avro'].call_args.args
        assert key == f'{SENSOR_ID}_{TIMESTAMP}'
        assert published['timestamp'] == TIMESTAMP

    # With no timestamp in the body the resource stamps one on, so the schema's
    # required timestamp field is always populated.
    def test_generates_timestamp_when_absent(self, client, auth_headers, mock_kafka):
        r = client.post(URL, headers=auth_headers, json=self._valid_body())

        assert r.status_code == 201
        _topic, _key, published, _schema = mock_kafka['avro'].call_args.args
        assert datetime.fromisoformat(published['timestamp']).tzinfo is not None

    # The reading is published to the sensor_readings topic with the Avro schema.
    def test_avro_message_uses_sensor_readings_topic(self, client, auth_headers, mock_kafka):
        client.post(URL, headers=auth_headers, json=self._valid_body())

        topic, _key, published, schema = mock_kafka['avro'].call_args.args
        assert topic == 'sensor_readings'
        assert published['sensor_id'] == SENSOR_ID
        assert 'SensorReading' in schema

    # After the reading is stored, a notification is published to sensor_uploaded.
    def test_notification_published_after_success(self, client, auth_headers, mock_kafka):
        client.post(URL, headers=auth_headers, json=self._valid_body(timestamp=TIMESTAMP))

        topic, key, notification = mock_kafka['simple'].call_args.args
        assert topic == 'sensor_reading_uploaded'
        assert key == f'{SENSOR_ID}_{TIMESTAMP}'
        assert notification['sensor_id'] == SENSOR_ID
        assert notification['event_type'] == 'sensor_reading_received'

    # Avro validation/publish failure returns 500 and skips the notification.
    def test_avro_failure_returns_500_and_skips_notification(self, client, auth_headers, mock_kafka):
        mock_kafka['avro'].return_value = False

        r = client.post(URL, headers=auth_headers, json=self._valid_body())

        assert r.status_code == 500
        assert r.get_json() == {'error': 'Failed to process reading'}
        mock_kafka['simple'].assert_not_called()
