import os

# Must set before importing api, since middleware.security captures the key at module import time.
os.environ.setdefault('API_SECRET_KEY', 'test-api-key')

import pytest

from api import app as flask_app


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


# Returns the API secret key for use in tests. This fixture is session-scoped, meaning it is created once per test session and shared across all tests that require it.
@pytest.fixture(scope='session')
def api_key():
    return os.environ['API_SECRET_KEY']

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
