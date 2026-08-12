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


@pytest.fixture()
def mock_db(request, mocker):
    """Factory for a mocked psycopg2 (conn, cursor) pair.

    Patches get_db_connection in the resource module named by the calling test
    module's RESOURCE_MODULE constant, e.g. RESOURCE_MODULE = 'resources.robot'.

    The returned mock supports both connection styles used across the resources:
    the direct one (`conn = get_db_connection(); cur = conn.cursor()`) and the
    context-manager one (`with get_db_connection() as conn, conn.cursor() as cur`).
    Getting that wrong is the main hazard of hand-rolling this mock per test file —
    without `cur.__enter__`, a `with conn.cursor() as cur` block silently binds a
    *different* MagicMock and the execute assertions fail as if the resource were
    broken.

    Args:
        fetchall: rows returned by cur.fetchall() (defaults to []).
        fetchone: row returned by cur.fetchone() (defaults to None).
        description: cur.description, a list of (column_name,) tuples. psycopg2's
            real description holds Column objects, but the resources only ever read
            desc[0], which a plain tuple satisfies.
        rowcount: cur.rowcount, which several resources check to detect a no-op write.

    Returns:
        A (conn, cursor) tuple of MagicMocks, so tests can assert on execute(),
        close() and friends.
    """
    target_resource = getattr(request.module, 'RESOURCE_MODULE', None)
    if target_resource is None:
        raise RuntimeError(
            f'{request.module.__name__} uses the mock_db fixture but does not define '
            "RESOURCE_MODULE (e.g. RESOURCE_MODULE = 'resources.robot')."
        )

    def _factory(fetchall=None, fetchone=None, description=None, rowcount=1):
        conn = mocker.MagicMock(name='pg_conn')
        conn.__enter__.return_value = conn
        conn.__exit__.return_value = False

        cur = mocker.MagicMock(name='pg_cursor')
        cur.__enter__.return_value = cur
        cur.__exit__.return_value = False
        cur.fetchall.return_value = fetchall if fetchall is not None else []
        cur.fetchone.return_value = fetchone
        cur.description = description
        cur.rowcount = rowcount

        conn.cursor.return_value = cur

        mocker.patch(f'{target_resource}.get_db_connection', return_value=conn)
        return conn, cur

    return _factory
