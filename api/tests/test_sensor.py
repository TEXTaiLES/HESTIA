"""Functional tests for SensorReadingResource (/sensor-readings)."""
from unittest.mock import MagicMock

VALID_READING = {"sensor_id": "Teapot-Sensor-1", "temperature": 21.5, "humidity": 40.0}


def test_get_requires_api_key(client):
    assert client.get("/sensor-readings").status_code == 401


def test_get_returns_rows(client, auth, mock_db):
    mock_db.description = [("sensor_id",), ("temperature",)]
    mock_db.fetchall.return_value = [("Teapot-Sensor-1", 21.5)]

    res = client.get("/sensor-readings?sensor_id=Teapot-Sensor-1", headers=auth)

    assert res.status_code == 200
    assert res.json == [{"sensor_id": "Teapot-Sensor-1", "temperature": 21.5}]
    sql, params = mock_db.execute.call_args.args
    assert "sensor_id = %s" in sql and "Teapot-Sensor-1" in params


def test_post_creates_reading(client, auth, monkeypatch):
    kafka = MagicMock(return_value=True)
    notify = MagicMock()
    monkeypatch.setattr("resources.sensor.send_avro_message", kafka)
    monkeypatch.setattr("resources.sensor.send_simple_message", notify)

    res = client.post("/sensor-readings", json=VALID_READING, headers=auth)

    assert res.status_code == 201
    assert res.json["id"].startswith("Teapot-Sensor-1_")
    kafka.assert_called_once()
    notify.assert_called_once()


def test_post_rejects_missing_required_fields(client, auth):
    res = client.post("/sensor-readings", json={"sensor_id": "Teapot-Sensor-1"}, headers=auth)
    assert res.status_code == 400


def test_post_returns_500_when_kafka_fails(client, auth, monkeypatch):
    monkeypatch.setattr("resources.sensor.send_avro_message", MagicMock(return_value=False))

    res = client.post("/sensor-readings", json=VALID_READING, headers=auth)

    assert res.status_code == 500
