"""Shared pytest fixtures. Anything defined here is auto-injected into tests by name."""
from unittest.mock import MagicMock

import pytest
from flask import Flask
from flask_restful import Api

import middleware.security as security
from resources.robot import RobotImageResource
from resources.sensor import SensorReadingResource

API_KEY = "test-key"


@pytest.fixture
def client(monkeypatch):
    """A Flask test client with the resources under test mounted on their real routes."""
    monkeypatch.setattr(security, "MASTER_API_KEY", API_KEY)
    app = Flask(__name__)
    api = Api(app)
    api.add_resource(SensorReadingResource, "/sensor-readings")
    api.add_resource(RobotImageResource, "/robot-images")
    return app.test_client()


@pytest.fixture
def auth():
    """Headers accepted by the require_api_key middleware."""
    return {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def mock_db(monkeypatch):
    """Replaces get_db_connection with a mock. Returns the cursor so tests can
    control query results through cursor.description / cursor.fetchall."""
    cursor = MagicMock()
    cursor.description = None
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn  # supports `with get_db_connection() as conn`
    cursor.__enter__.return_value = cursor  # supports `with conn.cursor() as cur`
    monkeypatch.setattr("resources.sensor.get_db_connection", lambda: conn)
    monkeypatch.setattr("resources.robot.get_db_connection", lambda: conn)
    return cursor
