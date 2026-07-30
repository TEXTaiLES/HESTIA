import os

# Must set before importing api, since middleware.security captures the key at module import time.
os.environ.setdefault('API_SECRET_KEY', 'test-api-key')

import pytest

from api import app as flask_app

# Returns the API secret key for use in tests. This fixture is session-scoped, meaning it is created once per test session and shared across all tests that require it.
@pytest.fixture(scope='session')
def api_key():
    return os.environ['API_SECRET_KEY']

# Enables the Flask app to be used in tests.
@pytest.fixture()
def app():
    flask_app.config.update(TESTING=True)
    return flask_app

# Create a test client for the Flask app, to make HTTP requests to the API without running a live server.
@pytest.fixture()
def client(app):
    return app.test_client()

# Provides a valid authorization header for API requests.
@pytest.fixture()
def auth_headers(api_key):
    return {'Authorization': f'Bearer {api_key}'}