from datetime import datetime, timezone

import pytest


RESOURCE_MODULE = 'resources.sensor_alert'

URL = '/sensor-alerts'
SENSOR_ID = 'Teapot-Sensor-1'
TIMESTAMP = '2026-01-01T00:00:00+00:00'
ALERT_ID = '11111111-2222-3333-4444-555555555555'

VALID_ALERT = {
    'sensor_id': SENSOR_ID,
    'timestamp': TIMESTAMP,
    'metric': 'humidity',
    'value': 71.3,
    'min_value': 45,
    'max_value': 55,
}

ROW_DESCRIPTION = [
    ('alert_id',), ('reading_id',), ('sensor_id',), ('reading_timestamp',),
    ('metric',), ('value',), ('min_value',), ('max_value',),
]
STORED_ROW = (ALERT_ID, f'{SENSOR_ID}_{TIMESTAMP}', SENSOR_ID, TIMESTAMP, 'humidity', 71.3, 45.0, 55.0)


@pytest.fixture()
def mock_kafka(mocker):
    return mocker.patch('resources.sensor_alert.send_simple_message', return_value=True)


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
        r = client.post(URL, json=VALID_ALERT)
        assert r.status_code == 401


class TestGet:
    # Rows are zipped against cur.description and returned as a JSON list.
    def test_returns_rows_as_json_list(self, client, auth_headers, mock_db):
        mock_db(fetchall=[STORED_ROW], description=ROW_DESCRIPTION)

        r = client.get(URL, headers=auth_headers)

        assert r.status_code == 200
        assert r.get_json()[0]['alert_id'] == ALERT_ID
        assert r.get_json()[0]['metric'] == 'humidity'

    # sensor_id, metric and the date range each add one bound clause, in that order.
    def test_filters(self, client, auth_headers, mock_db):
        _, cur = mock_db()
        client.get(
            f'{URL}?sensor_id={SENSOR_ID}&metric=humidity&start_date=2026-01-01&end_date=2026-02-01',
            headers=auth_headers,
        )

        sql, params = cur.execute.call_args.args
        assert 'AND sensor_id = %s' in sql
        assert 'AND metric = %s' in sql
        assert 'AND reading_timestamp >= %s' in sql
        assert 'AND reading_timestamp <= %s' in sql
        assert params[:4] == (SENSOR_ID, 'humidity', '2026-01-01', '2026-02-01')

    # Alerts come back newest reading first.
    def test_orders_by_reading_timestamp_descending(self, client, auth_headers, mock_db):
        _, cur = mock_db()
        client.get(URL, headers=auth_headers)

        sql, _params = cur.execute.call_args.args
        assert 'ORDER BY reading_timestamp DESC' in sql


class TestPost:
    def _valid_body(self, **overrides):
        body = dict(VALID_ALERT)
        body.update(overrides)
        return body

    # Empty JSON body triggers the "No data provided" 400 branch.
    def test_empty_body_returns_400(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db()

        r = client.post(URL, headers=auth_headers, json={})

        assert r.status_code == 400
        assert r.get_json() == {'error': 'No data provided'}
        cur.execute.assert_not_called()

    @pytest.mark.parametrize('missing', ['sensor_id', 'timestamp', 'metric', 'value'])
    def test_missing_required_field_returns_400(self, client, auth_headers, mock_db, mock_kafka, missing):
        _, cur = mock_db()
        body = {k: v for k, v in VALID_ALERT.items() if k != missing}

        r = client.post(URL, headers=auth_headers, json=body)

        assert r.status_code == 400
        assert 'Missing required fields' in r.get_json()['error']
        cur.execute.assert_not_called()

    def test_unknown_key_returns_400(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db()

        r = client.post(URL, headers=auth_headers, json=self._valid_body(nonsense='x'))

        assert r.status_code == 400
        assert r.get_json() == {'error': 'Not all given keys are valid'}
        cur.execute.assert_not_called()

    @pytest.mark.parametrize('field, value', [
        ('timestamp', 'yesterday'),
        ('value', 'high'),
        ('value', True),
        ('min_value', 'low'),
        ('max_value', [55]),
    ])
    def test_bad_value_types_return_400(self, client, auth_headers, mock_db, mock_kafka, field, value):
        _, cur = mock_db()

        r = client.post(URL, headers=auth_headers, json=self._valid_body(**{field: value}))

        assert r.status_code == 400
        cur.execute.assert_not_called()

    # Bounds are optional: a metric with only a max (e.g. luminosity) is fine.
    def test_null_bound_is_accepted(self, client, auth_headers, mock_db, mock_kafka):
        mock_db(fetchone=(ALERT_ID,), description=ROW_DESCRIPTION)

        r = client.post(URL, headers=auth_headers, json=self._valid_body(min_value=None))

        assert r.status_code == 201

    # A new alert is inserted, returned with 201, and announced on Kafka.
    def test_happy_path_returns_201_and_notifies(self, client, auth_headers, mock_db, mock_kafka):
        _, cur = mock_db(fetchone=(ALERT_ID,), description=ROW_DESCRIPTION)

        r = client.post(URL, headers=auth_headers, json=self._valid_body())

        assert r.status_code == 201
        body = r.get_json()
        assert body['message'] == 'Alert recorded'
        insert_sql, insert_params = cur.execute.call_args_list[0].args
        assert 'ON CONFLICT (sensor_id, reading_timestamp, metric) DO NOTHING' in insert_sql
        # reading_id defaults to the same "{sensor_id}_{timestamp}" id /sensor-readings returns.
        assert insert_params[1] == f'{SENSOR_ID}_{TIMESTAMP}'
        assert insert_params[3] == datetime(2026, 1, 1, tzinfo=timezone.utc)
        topic, key, notification = mock_kafka.call_args.args
        assert topic == 'sensor_alert_raised'
        assert key == body['id']
        assert notification['event_type'] == 'sensor_alert_raised'

    # Re-posting an alert that already exists is a 200 no-op without a notification.
    def test_duplicate_returns_200_without_notifying(self, client, auth_headers, mocker, mock_db, mock_kafka):
        _, cur = mock_db(description=ROW_DESCRIPTION)
        # INSERT ... RETURNING yields nothing on conflict; the follow-up SELECT finds the stored row.
        cur.fetchone.side_effect = [None, STORED_ROW]

        r = client.post(URL, headers=auth_headers, json=self._valid_body())

        assert r.status_code == 200
        assert r.get_json()['message'] == 'Alert already recorded'
        assert r.get_json()['id'] == ALERT_ID
        mock_kafka.assert_not_called()

    def test_db_error_returns_500(self, client, auth_headers, mocker, mock_kafka):
        mocker.patch('resources.sensor_alert.get_db_connection', side_effect=Exception('db down'))

        r = client.post(URL, headers=auth_headers, json=self._valid_body())

        assert r.status_code == 500
        mock_kafka.assert_not_called()
