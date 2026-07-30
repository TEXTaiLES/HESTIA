import pytest

import middleware.security as security
from api import app as flask_app

API_KEY = 'test-api-key'


def pytest_ignore_collect(collection_path, config):
    """Skip the integration package entirely unless integration tests were asked for."""

    if collection_path.name != 'integration':
        return None

    markexpr = config.getoption('markexpr', default='') or ''
    if 'not integration' in markexpr:
        return True
    if 'integration' in markexpr:
        return None  # explicitly requested with `pytest -m integration`
    return None  # no -m at all: collect everything


@pytest.fixture(autouse=True)
def api_secret_key(monkeypatch):
    monkeypatch.setattr(security, 'MASTER_API_KEY', API_KEY)


@pytest.fixture(scope='session')
def api_key():
    return API_KEY

# Enables the Flask app to be used in tests, with its config restored afterwards.
@pytest.fixture()
def app():
    original_config = dict(flask_app.config)
    flask_app.config.update(TESTING=True)

    yield flask_app

    flask_app.config.clear()
    flask_app.config.update(original_config)

# Create a test client for the Flask app, to make HTTP requests to the API without running a live server.
@pytest.fixture()
def client(app):
    return app.test_client()

# Provides a valid authorization header for API requests.
@pytest.fixture()
def auth_headers(api_key):
    return {'Authorization': f'Bearer {api_key}'}
