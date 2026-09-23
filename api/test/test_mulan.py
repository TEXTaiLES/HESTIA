"""Unit tests for the MulAn /api/v1 resources.

Unlike the other resource tests, these don't send an API key: the MulAn
endpoints authenticate the Directus session cookie instead. Every request
therefore starts with a session lookup in dispatch_request, which consumes the
first cur.fetchone() (and, for a non-admin role, the first cur.fetchall()).
The _authed helper queues those rows ahead of whatever the handler reads.

rasterio is mocked throughout (see _raster): the TIFF parsing itself belongs to
GDAL, so the tests cover the branches the resource takes on top of it. ZIP
packages are real archives, since zipfile is stdlib and cheap.
"""
import io
import json
import os
import zipfile

import pytest
from rasterio.errors import RasterioIOError

from resources.mulan import COOKIE_NAME
from test.conftest import MULAN_USER_ID


RESOURCE_MODULE = 'resources.mulan'

URL = '/api/v1/multispectral/images'
SESSION_URL = '/api/v1/session'
IMAGE_ID = '4e42d27f-67db-4e28-8391-01e2c0708048'
OTHER_ID = '11111111-2222-3333-4444-555555555555'
FILENAME = 'sample-multispectral.tif'
OBJECT_KEY = f'{IMAGE_ID}/source.tif'
MASK_KEY = f'{IMAGE_ID}/mask-0000.tif'
CREATED_AT = '2026-08-25T14:10:00Z'
UPDATED_AT = '2026-08-26T10:30:00Z'
TOKEN = 'session-token'
ALL_CAPS = ('images_read', 'images_create', 'annotations_read', 'annotations_create', 'annotations_update')

# The permission rows a fully-privileged non-admin role would hold. Spelled out
# rather than derived from resources.mulan.CAPABILITIES, so a typo in that map
# is caught instead of being mirrored by the test.
ALL_PERMISSIONS = [
    ('multispectral_images', 'read'),
    ('multispectral_images', 'create'),
    ('annotations', 'read'),
    ('annotations', 'create'),
    ('annotations', 'update'),
]

CLASSES = [
    {'id': 0, 'name': 'Background', 'description': '', 'color': '#000000'},
    {'id': 1, 'name': 'Damage', 'description': '', 'color': '#E53935'},
]


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------

@pytest.fixture()
def mulan_db(mocker):
    """Mocked psycopg2 (conn, cursor) whose reads are driven by queues.

    mock_db from conftest returns one fixed row per cursor method, which isn't
    enough here: a single request runs the session query and then the handler's
    own queries against the same cursor. fetchone/fetchall take side_effect
    lists instead, so each call pops the next queued result.
    """
    def _factory(fetchone=(), fetchall=()):
        conn = mocker.MagicMock(name='pg_conn')
        conn.__enter__.return_value = conn
        conn.__exit__.return_value = False

        cur = mocker.MagicMock(name='pg_cursor')
        cur.__enter__.return_value = cur
        cur.__exit__.return_value = False
        cur.fetchone.side_effect = list(fetchone)
        cur.fetchall.side_effect = list(fetchall)

        conn.cursor.return_value = cur
        mocker.patch('resources.mulan.get_db_connection', return_value=conn)
        return conn, cur

    return _factory


@pytest.fixture()
def minio(mocker):
    """The MinIO singleton as the MulAn module sees it."""
    return mocker.patch('resources.mulan.minio_client')


@pytest.fixture()
def cookie(client):
    """Sends the Directus session cookie with every request from `client`."""
    client.set_cookie(COOKIE_NAME, TOKEN)
    return client


def _authed(mulan_db, mulan_session, fetchone=(), fetchall=(), admin=True, permissions=()):
    """Queue a resolved session in front of the handler's own query results."""
    user, permission_rows = mulan_session(admin=admin, permissions=permissions)
    ones = [user, *fetchone]
    manys = list(fetchall) if admin else [permission_rows, *fetchall]
    return mulan_db(fetchone=ones, fetchall=manys)


def _image_row(**overrides):
    """A row shaped like IMAGE_SELECT returns (RealDictCursor -> dict)."""
    row = {
        'image_id': IMAGE_ID,
        'filename': FILENAME,
        'width': 2048,
        'height': 1536,
        'channel_count': 5,
        'dtype': 'uint16',
        'channel_names': ['450nm', '550nm', '650nm', '750nm', '850nm'],
        'georeferenced': False,
        'size_bytes': 31457280,
        'created_at': CREATED_AT,
        'created_by': MULAN_USER_ID,
        'created_by_name': 'Example User',
        'annotation_id': None,
        'annotation_updated_at': None,
    }
    row.update(overrides)
    return row


def _annotated_row(**overrides):
    """A row shaped like ANNOTATED_IMAGE_SQL returns."""
    row = {
        'image_id': IMAGE_ID,
        'width': 2048,
        'height': 1536,
        'crs': None,
        'transform': None,
        'annotation_id': None,
        'revision': None,
        'annotation_metadata': None,
        'mask_object_key': None,
        'mask_dtype': None,
        'annotation_created_at': None,
        'annotation_updated_at': None,
    }
    row.update(overrides)
    return row


def _with_annotation(**overrides):
    row = _annotated_row(
        annotation_id='aeef9456-54f3-4bbd-87cb-a75f9f1b9a86',
        revision=3,
        annotation_metadata={'format': 'mulan-semantic-segmentation', 'version': '1.0', 'classes': CLASSES},
        mask_object_key=MASK_KEY,
        mask_dtype='uint8',
        annotation_created_at=CREATED_AT,
        annotation_updated_at=UPDATED_AT,
    )
    row.update(overrides)
    return row


def _raster(mocker, width=2048, height=1536, count=5, dtypes=None, driver='GTiff',
            descriptions=None, crs=None, transform=None, values=(0, 1)):
    """Patch rasterio.open with a dataset whose attributes the resource reads."""
    dataset = mocker.MagicMock(name='raster')
    dataset.__enter__.return_value = dataset
    dataset.__exit__.return_value = False
    dataset.driver = driver
    dataset.width = width
    dataset.height = height
    dataset.count = count
    dataset.dtypes = dtypes or tuple(['uint16'] * count)
    dataset.descriptions = descriptions or tuple(f'{350 + b * 100}nm' for b in range(1, count + 1))
    dataset.crs = crs
    dataset.transform = transform
    # The mask path walks block windows and unions the pixel values it finds.
    dataset.block_windows.return_value = [((0, 0), 'window')]
    dataset.read.return_value = __import__('numpy').array(values)
    return mocker.patch('resources.mulan.rasterio.open', return_value=dataset)


def _tiff(name=FILENAME, content=b'II*\x00fake-tiff'):
    return {'file': (io.BytesIO(content), name)}


def _package(entries=None, metadata=None, mask=b'II*\x00fake-mask'):
    """A real ZIP archive; entries overrides the member names."""
    metadata = metadata if metadata is not None else {
        'format': 'mulan-semantic-segmentation',
        'version': '1.0',
        'image': {'width': 2048, 'height': 1536},
        'mask': {'width': 2048, 'height': 1536, 'dtype': 'uint8', 'background_id': 0, 'ignore_id': None},
        'classes': CLASSES,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        if entries is None:
            archive.writestr('mask.tif', mask)
            archive.writestr('annotations.json', json.dumps(metadata))
        else:
            for name in entries:
                archive.writestr(name, json.dumps(metadata) if name.endswith('.json') else mask)
    buffer.seek(0)
    return buffer


def _put_body(package=None, **form):
    return {'package': (package or _package(), 'package.zip'), **form}


def assert_error(response, status, code):
    """Every MulAn failure uses the same envelope."""
    assert response.status_code == status
    body = response.get_json()
    assert set(body) == {'error'}
    assert set(body['error']) == {'code', 'message', 'details'}
    assert body['error']['code'] == code
    assert isinstance(body['error']['message'], str) and body['error']['message']
    assert isinstance(body['error']['details'], dict)
    return body['error']


def executed(cur, fragment):
    """The first execute() call whose SQL contains `fragment`."""
    for call in cur.execute.call_args_list:
        if fragment in call.args[0]:
            return call
    raise AssertionError(f'no execute() containing {fragment!r}; got: '
                         f'{[c.args[0][:60] for c in cur.execute.call_args_list]}')


# --------------------------------------------------------------------------
# dispatch_request: uuid, origin, session, capabilities, failures
# --------------------------------------------------------------------------

class TestAuth:
    # A path parameter that isn't a UUID is rejected before the row is looked up.
    def test_bad_uuid_returns_400(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session)

        r = cookie.get(f'{URL}/not-a-uuid')

        assert_error(r, 400, 'invalid_image_id')

    # The format check precedes authentication, so an anonymous caller sees the
    # same 400 — and learns nothing about whether any image exists.
    def test_bad_uuid_without_cookie_returns_400(self, client, mulan_db):
        mulan_db()

        r = client.get(f'{URL}/not-a-uuid')

        assert_error(r, 400, 'invalid_image_id')

    def test_missing_cookie_returns_401(self, client, mulan_db):
        mulan_db()

        r = client.get(URL)

        assert_error(r, 401, 'unauthenticated')

    # A cookie whose token matches no live session row is treated as anonymous.
    def test_unknown_token_returns_401(self, cookie, mulan_db):
        mulan_db(fetchone=[None])

        r = cookie.get(URL)

        assert_error(r, 401, 'unauthenticated')

    # admin_access short-circuits the permission lookup: one query, all capabilities.
    def test_admin_access_grants_every_capability(self, cookie, mulan_db, mulan_session):
        _, cur = _authed(mulan_db, mulan_session, admin=True)

        r = cookie.get(SESSION_URL)

        assert r.status_code == 200
        assert r.get_json()['capabilities'] == {cap: True for cap in ALL_CAPS}
        assert cur.execute.call_count == 1  # no directus_permissions query

    # A normal role's permission rows map onto the five capabilities.
    def test_non_admin_maps_permission_rows(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session, admin=False,
                permissions=[('multispectral_images', 'read'), ('annotations', 'read')])

        r = cookie.get(SESSION_URL)

        assert r.get_json()['capabilities'] == {
            'images_read': True,
            'images_create': False,
            'annotations_read': True,
            'annotations_create': False,
            'annotations_update': False,
        }

    # Read access doesn't imply write access: POST needs images_create.
    def test_missing_capability_returns_403(self, cookie, mulan_db, mulan_session, minio):
        _authed(mulan_db, mulan_session, admin=False,
                permissions=[('multispectral_images', 'read')])

        r = cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        assert_error(r, 403, 'forbidden')
        minio.fput_object.assert_not_called()

    # Same session, a capability it does hold: the handler runs.
    def test_present_capability_passes(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session, admin=False,
                permissions=[('multispectral_images', 'read')],
                fetchone=[{'total': 0}], fetchall=[[]])

        r = cookie.get(URL)

        assert r.status_code == 200


class TestOrigin:
    # A page on another site cannot use the visitor's cookie to write.
    @pytest.mark.parametrize('method,path,data', [
        ('post', URL, _tiff),
        ('put', f'{URL}/{IMAGE_ID}/annotation', _put_body),
    ])
    def test_foreign_origin_is_rejected(self, cookie, mulan_db, mulan_session, minio, method, path, data):
        _authed(mulan_db, mulan_session)

        r = getattr(cookie, method)(path, data=data(), content_type='multipart/form-data',
                                    headers={'Origin': 'https://evil.example'})

        assert_error(r, 403, 'origin_not_allowed')
        minio.fput_object.assert_not_called()

    # An origin the deployment configured (MULAN_ORIGINS) is let through.
    def test_allowed_origin_passes(self, cookie, mulan_db, mulan_session, mocker):
        mocker.patch('resources.mulan.ALLOWED_ORIGINS', ['https://mulan.example.org'])
        _authed(mulan_db, mulan_session)

        r = cookie.post(URL, data={'filename': 'x.tif'}, content_type='multipart/form-data',
                        headers={'Origin': 'https://mulan.example.org'})

        assert_error(r, 400, 'missing_file')  # reached the handler

    # curl, a MulAn backend proxy and the smoke test send no Origin at all.
    def test_missing_origin_passes(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session)

        r = cookie.post(URL, data={'filename': 'x.tif'}, content_type='multipart/form-data')

        assert_error(r, 400, 'missing_file')

    # Reads are not CSRF vectors, so GET ignores the header.
    def test_get_ignores_origin(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session, fetchone=[{'total': 0}], fetchall=[[]])

        r = cookie.get(URL, headers={'Origin': 'https://evil.example'})

        assert r.status_code == 200


class TestDispatchFailures:
    # An unreachable database is an internal error in the MulAn envelope, not an HTML 500.
    def test_db_failure_returns_500(self, cookie, mocker):
        mocker.patch('resources.mulan.get_db_connection', side_effect=Exception('down'))

        r = cookie.get(URL)

        assert_error(r, 500, 'internal_error')

    # Werkzeug aborts the upload once the body passes the per-request limit.
    def test_oversized_upload_returns_413(self, cookie, mulan_db, mulan_session, mocker):
        mocker.patch('resources.mulan.MAX_UPLOAD_BYTES', 64)
        _authed(mulan_db, mulan_session)

        r = cookie.post(URL, data=_tiff(content=b'x' * 5000), content_type='multipart/form-data')

        assert_error(r, 413, 'payload_too_large')


# --------------------------------------------------------------------------
# GET /session
# --------------------------------------------------------------------------

class TestSession:
    # The one endpoint MulAn may call before the user logs in.
    def test_anonymous_body(self, client, mulan_db):
        mulan_db()

        r = client.get(SESSION_URL)
        body = r.get_json()

        assert r.status_code == 200
        assert body['authenticated'] is False
        assert body['user'] is None
        assert body['capabilities'] == {cap: False for cap in ALL_CAPS}
        assert body['login_url'].endswith('/archive/user/login')
        assert body['egi_login_url'].endswith('/archive/user/egi-login')

    def test_authenticated_body(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session)

        body = cookie.get(SESSION_URL).get_json()

        assert body['authenticated'] is True
        assert body['user'] == {
            'id': MULAN_USER_ID,
            'email': 'user@example.org',
            'display_name': 'Example User',
        }
        assert body['logout_url'].endswith('/archive/user/logout')

    # The response depends on the cookie, so it must not be cached or shared.
    def test_cache_headers(self, client, mulan_db):
        mulan_db()

        r = client.get(SESSION_URL)

        assert r.headers['Cache-Control'] == 'no-store'
        assert r.headers['Vary'] == 'Cookie'


# --------------------------------------------------------------------------
# GET /multispectral/images
# --------------------------------------------------------------------------

class TestList:
    def test_invalid_has_annotation_returns_400(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session)

        r = cookie.get(f'{URL}?has_annotation=maybe')

        assert_error(r, 400, 'invalid_boolean')

    def test_invalid_sort_returns_400(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session)

        r = cookie.get(f'{URL}?sort=nonsense')

        assert_error(r, 400, 'invalid_sort')

    # One row asserts the whole item contract MulAn's selection modal reads.
    def test_item_shape(self, cookie, mulan_db, mulan_session):
        row = _image_row(annotation_id='ann-1', annotation_updated_at=UPDATED_AT)
        _authed(mulan_db, mulan_session, fetchone=[{'total': 1}], fetchall=[[row]])

        body = cookie.get(URL).get_json()

        assert body['items'] == [{
            'image_id': IMAGE_ID,
            'filename': FILENAME,
            'width': 2048,
            'height': 1536,
            'channel_count': 5,
            'dtype': 'uint16',
            'channel_names': ['450nm', '550nm', '650nm', '750nm', '850nm'],
            'georeferenced': False,
            'size_bytes': 31457280,
            'has_annotation': True,
            'annotation_updated_at': UPDATED_AT,
            'created_at': CREATED_AT,
            'created_by': {'id': MULAN_USER_ID, 'display_name': 'Example User'},
            'file_url': f'{URL}/{IMAGE_ID}/file',
        }]

    # total comes from the separate count query, not from len(items) — the page
    # of rows is a window onto a larger result.
    def test_total_comes_from_count_query(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session, fetchone=[{'total': 137}], fetchall=[[_image_row()]])

        body = cookie.get(URL).get_json()

        assert body['total'] == 137
        assert len(body['items']) == 1

    # No matches is an empty page, not a 204.
    def test_empty_result_returns_200(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session, fetchone=[{'total': 0}], fetchall=[[]])

        r = cookie.get(URL)

        assert r.status_code == 200
        assert r.get_json()['items'] == []

    # Rows predating the ownership column (or created server-side) have no user.
    def test_created_by_null(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session, fetchone=[{'total': 1}],
                fetchall=[[_image_row(created_by=None, created_by_name=None)]])

        body = cookie.get(URL).get_json()

        assert body['items'][0]['created_by'] is None


# --------------------------------------------------------------------------
# POST /multispectral/images
# --------------------------------------------------------------------------

class TestUpload:
    def test_missing_file_returns_400(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session)

        r = cookie.post(URL, data={'filename': 'x.tif'}, content_type='multipart/form-data')

        assert_error(r, 400, 'missing_file')

    def test_empty_filename_returns_400(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session)

        r = cookie.post(URL, data={'file': (io.BytesIO(b'x'), '')}, content_type='multipart/form-data')

        assert_error(r, 400, 'missing_file')

    @pytest.mark.parametrize('name', ['photo.png', 'archive.zip', 'noextension'])
    def test_wrong_extension_returns_415(self, cookie, mulan_db, mulan_session, name):
        _authed(mulan_db, mulan_session)

        r = cookie.post(URL, data=_tiff(name=name), content_type='multipart/form-data')

        assert_error(r, 415, 'unsupported_media_type')

    @pytest.mark.parametrize('name', ['SAMPLE.TIF', 'sample.TIFF'])
    def test_extension_check_is_case_insensitive(self, cookie, mulan_db, mulan_session, mocker, minio, name):
        _authed(mulan_db, mulan_session, fetchone=[_image_row()])
        _raster(mocker)

        r = cookie.post(URL, data=_tiff(name=name), content_type='multipart/form-data')

        assert r.status_code == 201

    # GDAL can't read it: not a TIFF whatever the extension says.
    def test_unreadable_tiff_returns_422(self, cookie, mulan_db, mulan_session, mocker, minio):
        _authed(mulan_db, mulan_session)
        mocker.patch('resources.mulan.rasterio.open', side_effect=RasterioIOError('nope'))

        r = cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        assert_error(r, 422, 'invalid_tiff')
        minio.fput_object.assert_not_called()

    # A PNG renamed to .tif opens fine in GDAL, but its driver gives it away.
    def test_non_gtiff_driver_returns_422(self, cookie, mulan_db, mulan_session, mocker, minio):
        _authed(mulan_db, mulan_session)
        _raster(mocker, driver='PNG')

        r = cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        assert_error(r, 422, 'invalid_tiff')

    def test_unsupported_dtype_returns_422(self, cookie, mulan_db, mulan_session, mocker, minio):
        _authed(mulan_db, mulan_session)
        _raster(mocker, count=1, dtypes=('complex64',))

        r = cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        assert_error(r, 422, 'invalid_tiff')

    # Every stored property is read off the raster, never off the request.
    def test_raster_metadata_reaches_the_insert(self, cookie, mulan_db, mulan_session, mocker, minio):
        _, cur = _authed(mulan_db, mulan_session, fetchone=[_image_row()])
        _raster(mocker, width=640, height=480, count=3,
                dtypes=('uint8', 'uint8', 'uint8'), descriptions=('r', 'g', 'b'))

        cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        params = executed(cur, 'INSERT INTO multispectral_images').args[1]
        image_id, filename, object_key, size_bytes, width, height, channel_count, dtype = params[:8]
        channel_names, georeferenced, crs = params[8:11]
        assert (width, height, channel_count, dtype) == (640, 480, 3, 'uint8')
        assert channel_names.adapted == ['r', 'g', 'b']
        assert georeferenced is False and crs is None
        assert object_key == f'{image_id}/source.tif'
        assert size_bytes > 0

    # Bands can disagree; the row then records 'mixed' rather than one of them.
    def test_mixed_dtypes_store_mixed(self, cookie, mulan_db, mulan_session, mocker, minio):
        _, cur = _authed(mulan_db, mulan_session, fetchone=[_image_row()])
        _raster(mocker, count=2, dtypes=('uint8', 'uint16'))

        cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        assert executed(cur, 'INSERT INTO multispectral_images').args[1][7] == 'mixed'

    # A traversal-flavoured name is flattened before it reaches the DB or a header.
    def test_filename_is_sanitised(self, cookie, mulan_db, mulan_session, mocker, minio):
        _, cur = _authed(mulan_db, mulan_session, fetchone=[_image_row()])
        _raster(mocker)

        cookie.post(URL, data={**_tiff(), 'filename': '../../etc/passwd.tif'},
                    content_type='multipart/form-data')

        stored = executed(cur, 'INSERT INTO multispectral_images').args[1][1]
        assert '/' not in stored and '..' not in stored

    def test_success_returns_201_and_location(self, cookie, mulan_db, mulan_session, mocker, minio):
        # The id is generated server-side, so pin it to keep the canned row in step.
        mocker.patch('resources.mulan.uuid.uuid4', return_value=IMAGE_ID)
        _authed(mulan_db, mulan_session, fetchone=[_image_row()])
        _raster(mocker)

        r = cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        assert r.status_code == 201
        body = r.get_json()
        assert body['image_id'] == IMAGE_ID
        assert r.headers['Location'] == f'{URL}/{IMAGE_ID}'
        assert body['has_annotation'] is False

    # One object per image, keyed by its UUID — not one object per band.
    def test_object_key_and_content_type(self, cookie, mulan_db, mulan_session, mocker, minio):
        _, cur = _authed(mulan_db, mulan_session, fetchone=[_image_row()])
        _raster(mocker)

        cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        bucket, key, path = minio.fput_object.call_args.args
        image_id = executed(cur, 'INSERT INTO multispectral_images').args[1][0]
        assert bucket == 'multispectral'
        assert key == f'{image_id}/source.tif'
        assert minio.fput_object.call_args.kwargs['content_type'] == 'image/tiff'

    # Without the commit the row vanishes while the object stays behind.
    def test_insert_is_committed(self, cookie, mulan_db, mulan_session, mocker, minio):
        conn, _ = _authed(mulan_db, mulan_session, fetchone=[_image_row()])
        _raster(mocker)

        cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        assert conn.__exit__.called, 'the connection was closed without committing the transaction'

    def test_db_failure_returns_storage_error(self, cookie, mulan_db, mulan_session, mocker, minio):
        _, cur = _authed(mulan_db, mulan_session, fetchone=[_image_row()])
        cur.execute.side_effect = [None, Exception('insert failed')]
        _raster(mocker)

        r = cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        assert_error(r, 500, 'storage_error')

    def test_minio_failure_returns_storage_error(self, cookie, mulan_db, mulan_session, mocker, minio):
        _, cur = _authed(mulan_db, mulan_session, fetchone=[_image_row()])
        _raster(mocker)
        minio.fput_object.side_effect = Exception('minio down')

        r = cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        assert_error(r, 500, 'storage_error')
        assert not any('INSERT INTO multispectral_images' in c.args[0] for c in cur.execute.call_args_list)

    # The spooled copy of a 500 MB upload must not survive the request.
    @pytest.mark.parametrize('fail', [False, True])
    def test_temp_directory_is_removed(self, cookie, mulan_db, mulan_session, mocker, minio, fail):
        _authed(mulan_db, mulan_session, fetchone=[_image_row()])
        _raster(mocker)
        if fail:
            minio.fput_object.side_effect = Exception('minio down')
        created = []
        real_mkdtemp = __import__('tempfile').mkdtemp
        mocker.patch('resources.mulan.tempfile.mkdtemp',
                     side_effect=lambda **kw: created.append(real_mkdtemp(**kw)) or created[-1])

        cookie.post(URL, data=_tiff(), content_type='multipart/form-data')

        assert created and not os.path.exists(created[0])


# --------------------------------------------------------------------------
# GET /multispectral/images/{id} and /file
# --------------------------------------------------------------------------

class TestImageItem:
    def test_unknown_image_returns_404(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session, fetchone=[None])

        r = cookie.get(f'{URL}/{IMAGE_ID}')

        assert_error(r, 404, 'image_not_found')

    # Same serializer as the list, so the two representations can't drift apart.
    def test_body_matches_list_item_shape(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session, fetchone=[_image_row()])
        item = cookie.get(f'{URL}/{IMAGE_ID}').get_json()

        _authed(mulan_db, mulan_session, fetchone=[{'total': 1}], fetchall=[[_image_row()]])
        listed = cookie.get(URL).get_json()['items'][0]

        assert item == listed

    def test_queries_by_image_id(self, cookie, mulan_db, mulan_session):
        _, cur = _authed(mulan_db, mulan_session, fetchone=[_image_row()])

        cookie.get(f'{URL}/{IMAGE_ID}')

        assert executed(cur, 'FROM multispectral_images').args[1] == (IMAGE_ID,)


class TestImageFile:
    @pytest.fixture()
    def stream(self, mocker):
        """stream_object returns a real Response, so header handling is exercised."""
        from flask import Response
        return mocker.patch(
            'resources.mulan.stream_object',
            return_value=Response(b'tiff-bytes', mimetype='image/tiff',
                                  headers={'Content-Disposition': f'attachment; filename="{FILENAME}"'}),
        )

    def test_unknown_image_returns_404_without_touching_minio(self, cookie, mulan_db, mulan_session, minio):
        _authed(mulan_db, mulan_session, fetchone=[None])

        r = cookie.get(f'{URL}/{IMAGE_ID}/file')

        assert_error(r, 404, 'image_not_found')
        minio.stat_object.assert_not_called()

    def test_caching_headers(self, cookie, mulan_db, mulan_session, minio, stream):
        _authed(mulan_db, mulan_session, fetchone=[{'filename': FILENAME, 'object_key': OBJECT_KEY}])
        minio.stat_object.return_value.etag = 'abc123'
        minio.stat_object.return_value.size = 31457280

        r = cookie.get(f'{URL}/{IMAGE_ID}/file')

        assert r.status_code == 200
        assert r.headers['ETag'] == '"abc123"'
        assert r.headers['Cache-Control'] == 'private'
        assert r.headers['Content-Length'] == '31457280'

    def test_content_type_and_filename(self, cookie, mulan_db, mulan_session, minio, stream):
        _authed(mulan_db, mulan_session, fetchone=[{'filename': FILENAME, 'object_key': OBJECT_KEY}])
        minio.stat_object.return_value.etag = 'abc123'
        minio.stat_object.return_value.size = 10

        r = cookie.get(f'{URL}/{IMAGE_ID}/file')

        assert r.headers['Content-Type'] == 'image/tiff'
        assert FILENAME in r.headers['Content-Disposition']
        assert stream.call_args.args == ('multispectral', OBJECT_KEY)

    # A browser that already holds the file revalidates instead of re-downloading.
    def test_matching_etag_returns_304(self, cookie, mulan_db, mulan_session, minio, stream):
        _authed(mulan_db, mulan_session, fetchone=[{'filename': FILENAME, 'object_key': OBJECT_KEY}])
        minio.stat_object.return_value.etag = 'abc123'
        minio.stat_object.return_value.size = 10

        r = cookie.get(f'{URL}/{IMAGE_ID}/file', headers={'If-None-Match': '"abc123"'})

        assert r.status_code == 304
        assert r.data == b''
        stream.assert_not_called()


# --------------------------------------------------------------------------
# GET /multispectral/images/{id}/annotation
# --------------------------------------------------------------------------

class TestAnnotationGet:
    def test_body_shape(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session, fetchone=[_with_annotation()])

        r = cookie.get(f'{URL}/{IMAGE_ID}/annotation')

        assert r.status_code == 200
        assert r.get_json() == {
            'annotation_id': 'aeef9456-54f3-4bbd-87cb-a75f9f1b9a86',
            'multispectral_image_id': IMAGE_ID,
            'format': 'mulan-semantic-segmentation',
            'format_version': '1.0',
            'revision': 3,
            'classes': CLASSES,
            'mask': {
                'width': 2048,
                'height': 1536,
                'dtype': 'uint8',
                'background_id': 0,
                'ignore_id': None,
            },
            'created_at': CREATED_AT,
            'updated_at': UPDATED_AT,
            'package_url': f'{URL}/{IMAGE_ID}/annotation/package',
        }


# --------------------------------------------------------------------------
# PUT /multispectral/images/{id}/annotation
# --------------------------------------------------------------------------

class TestAnnotationPut:
    def test_missing_package_returns_400(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session)

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation', data={'overwrite': 'true'},
                       content_type='multipart/form-data')

        assert_error(r, 400, 'missing_package')

    def test_bad_expected_revision_returns_400(self, cookie, mulan_db, mulan_session):
        _authed(mulan_db, mulan_session)

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(expected_revision='abc'),
                       content_type='multipart/form-data')

        assert_error(r, 400, 'invalid_expected_revision')

    # Masks can only be stored against an image HESTIA already holds.
    def test_unknown_image_returns_404(self, cookie, mulan_db, mulan_session, minio):
        _authed(mulan_db, mulan_session, fetchone=[None])

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(),
                       content_type='multipart/form-data')

        assert_error(r, 404, 'image_not_found')
        minio.fput_object.assert_not_called()

    def test_not_a_zip_returns_415(self, cookie, mulan_db, mulan_session, minio):
        _authed(mulan_db, mulan_session, fetchone=[_annotated_row()])

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation',
                       data={'package': (io.BytesIO(b'not a zip at all'), 'package.zip')},
                       content_type='multipart/form-data')

        assert_error(r, 415, 'unsupported_media_type')

    # Exactly two members at the archive root: no source image, no nesting, no traversal.
    @pytest.mark.parametrize('entries', [
        ['mask.tif', 'annotations.json', 'image.tif'],
        ['mask.tif'],
        ['masks/mask.tif', 'annotations.json'],
        ['../mask.tif', 'annotations.json'],
    ])
    def test_wrong_entry_set_returns_422(self, cookie, mulan_db, mulan_session, minio, entries):
        _authed(mulan_db, mulan_session, fetchone=[_annotated_row()])

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(_package(entries=entries)),
                       content_type='multipart/form-data')

        assert_error(r, 422, 'invalid_annotation_package')
        minio.fput_object.assert_not_called()

    # The declared expanded size is checked before anything is written out.
    def test_oversized_expansion_returns_413(self, cookie, mulan_db, mulan_session, mocker, minio):
        mocker.patch('resources.mulan.MAX_PACKAGE_EXPANDED_BYTES', 10)
        _authed(mulan_db, mulan_session, fetchone=[_annotated_row()])

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(),
                       content_type='multipart/form-data')

        assert_error(r, 413, 'payload_too_large')

    @pytest.mark.parametrize('kwargs', [
        {'count': 3},                      # not a single-band mask
        {'count': 1, 'dtypes': ('float32',)},  # class ids must be integers
    ])
    def test_bad_mask_raster_returns_422(self, cookie, mulan_db, mulan_session, mocker, minio, kwargs):
        _authed(mulan_db, mulan_session, fetchone=[_annotated_row()])
        _raster(mocker, **kwargs)

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(),
                       content_type='multipart/form-data')

        assert_error(r, 422, 'invalid_annotation_package')

    def test_unreadable_mask_returns_422(self, cookie, mulan_db, mulan_session, mocker, minio):
        _authed(mulan_db, mulan_session, fetchone=[_annotated_row()])
        mocker.patch('resources.mulan.rasterio.open', side_effect=RasterioIOError('nope'))

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(),
                       content_type='multipart/form-data')

        assert_error(r, 422, 'invalid_annotation_package')

    # A mask that doesn't line up with the image would be meaningless.
    def test_dimension_mismatch_returns_422_with_details(self, cookie, mulan_db, mulan_session, mocker, minio):
        _authed(mulan_db, mulan_session, fetchone=[_annotated_row()])
        _raster(mocker, width=100, height=50, count=1, dtypes=('uint8',))

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(),
                       content_type='multipart/form-data')

        error = assert_error(r, 422, 'mask_dimension_mismatch')
        assert error['details'] == {'expected': [2048, 1536], 'actual': [100, 50]}

    # Every id painted into the mask needs a class definition to go with it.
    def test_undefined_class_ids_return_metadata_conflict(self, cookie, mulan_db, mulan_session, mocker, minio):
        _authed(mulan_db, mulan_session, fetchone=[_annotated_row()])
        _raster(mocker, count=1, dtypes=('uint8',), values=(0, 1, 7))

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(),
                       content_type='multipart/form-data')

        error = assert_error(r, 422, 'metadata_conflict')
        assert error['details'] == {'undefined_ids': [7]}

    def test_create_returns_201_with_insert_parameters(self, cookie, mulan_db, mulan_session, mocker, minio):
        _, cur = _authed(mulan_db, mulan_session,
                         fetchone=[_annotated_row(), _annotated_row(), _with_annotation()])
        _raster(mocker, count=1, dtypes=('uint8',))

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(),
                       content_type='multipart/form-data')

        assert r.status_code == 201
        params = executed(cur, 'INSERT INTO annotations').args[1]
        assert params[2] == IMAGE_ID          # multispectral_image_id
        assert params[3].startswith(f'{IMAGE_ID}/mask-')  # mask_object_key
        assert params[4] == 'uint8'           # mask_dtype

    # An existing annotation is never replaced silently.
    def test_existing_without_overwrite_returns_409(self, cookie, mulan_db, mulan_session, mocker, minio):
        _, cur = _authed(mulan_db, mulan_session,
                         fetchone=[_with_annotation(), _with_annotation()])
        _raster(mocker, count=1, dtypes=('uint8',))

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(),
                       content_type='multipart/form-data')

        error = assert_error(r, 409, 'annotation_exists')
        assert error['details'] == {
            'annotation_id': 'aeef9456-54f3-4bbd-87cb-a75f9f1b9a86',
            'revision': 3,
            'updated_at': UPDATED_AT,
        }
        assert not any('UPDATE annotations' in c.args[0] for c in cur.execute.call_args_list)

    # Someone else saved in the meantime: the stale client is told, not merged over.
    def test_stale_expected_revision_returns_409(self, cookie, mulan_db, mulan_session, mocker, minio):
        _, cur = _authed(mulan_db, mulan_session,
                         fetchone=[_with_annotation(), _with_annotation()])
        _raster(mocker, count=1, dtypes=('uint8',))

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation',
                       data=_put_body(overwrite='true', expected_revision='1'),
                       content_type='multipart/form-data')

        assert_error(r, 409, 'annotation_revision_conflict')
        assert not any('UPDATE annotations' in c.args[0] for c in cur.execute.call_args_list)

    def test_overwrite_returns_200_and_bumps_revision(self, cookie, mulan_db, mulan_session, mocker, minio):
        _, cur = _authed(mulan_db, mulan_session,
                         fetchone=[_with_annotation(), _with_annotation(), _with_annotation(revision=4)])
        _raster(mocker, count=1, dtypes=('uint8',))

        r = cookie.put(f'{URL}/{IMAGE_ID}/annotation',
                       data=_put_body(overwrite='true', expected_revision='3'),
                       content_type='multipart/form-data')

        assert r.status_code == 200
        update = executed(cur, 'UPDATE annotations')
        assert 'revision = revision + 1' in update.args[0]
        assert update.args[1][-1] == 'aeef9456-54f3-4bbd-87cb-a75f9f1b9a86'

    # A fresh key per revision: the old object stays readable until the commit lands.
    def test_mask_uploaded_under_a_fresh_key(self, cookie, mulan_db, mulan_session, mocker, minio):
        _authed(mulan_db, mulan_session,
                fetchone=[_with_annotation(), _with_annotation(), _with_annotation(revision=4)])
        _raster(mocker, count=1, dtypes=('uint8',))

        cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(overwrite='true'),
                   content_type='multipart/form-data')

        bucket, key, _ = minio.fput_object.call_args.args
        assert bucket == 'annotations'
        assert key.startswith(f'{IMAGE_ID}/mask-') and key != MASK_KEY

    def test_old_mask_removed_after_overwrite(self, cookie, mulan_db, mulan_session, mocker, minio):
        _authed(mulan_db, mulan_session,
                fetchone=[_with_annotation(), _with_annotation(), _with_annotation(revision=4)])
        _raster(mocker, count=1, dtypes=('uint8',))

        cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(overwrite='true'),
                   content_type='multipart/form-data')

        minio.remove_object.assert_called_once_with('annotations', MASK_KEY)

    # A refused overwrite must not leave its uploaded mask behind.
    def test_unused_mask_removed_when_write_is_refused(self, cookie, mulan_db, mulan_session, mocker, minio):
        _authed(mulan_db, mulan_session, fetchone=[_with_annotation(), _with_annotation()])
        _raster(mocker, count=1, dtypes=('uint8',))

        cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(),
                   content_type='multipart/form-data')

        uploaded = minio.fput_object.call_args.args[1]
        minio.remove_object.assert_called_once_with('annotations', uploaded)

    def test_write_is_committed(self, cookie, mulan_db, mulan_session, mocker, minio):
        conn, _ = _authed(mulan_db, mulan_session,
                          fetchone=[_annotated_row(), _annotated_row(), _with_annotation()])
        _raster(mocker, count=1, dtypes=('uint8',))

        cookie.put(f'{URL}/{IMAGE_ID}/annotation', data=_put_body(),
                   content_type='multipart/form-data')

        assert conn.__exit__.called, 'the connection was closed without committing the transaction'


# --------------------------------------------------------------------------
# GET /multispectral/images/{id}/annotation/package
# --------------------------------------------------------------------------

class TestAnnotationPackage:
    @pytest.fixture()
    def stored_mask(self, minio):
        """MinIO hands back a urllib3-style response the resource copies and closes."""
        minio.get_object.return_value = io.BytesIO(b'II*\x00mask-bytes')
        minio.get_object.return_value.release_conn = lambda: None
        return minio

    def test_unknown_image_returns_404(self, cookie, mulan_db, mulan_session, minio):
        _authed(mulan_db, mulan_session, fetchone=[None])

        r = cookie.get(f'{URL}/{IMAGE_ID}/annotation/package')

        assert_error(r, 404, 'image_not_found')
        minio.get_object.assert_not_called()

    def test_missing_annotation_returns_404(self, cookie, mulan_db, mulan_session, minio):
        _authed(mulan_db, mulan_session, fetchone=[_annotated_row()])

        r = cookie.get(f'{URL}/{IMAGE_ID}/annotation/package')

        assert_error(r, 404, 'annotation_not_found')
        minio.get_object.assert_not_called()

    # MulAn's importer expects these two members and nothing else.
    def test_zip_contains_exactly_two_members(self, cookie, mulan_db, mulan_session, stored_mask):
        _authed(mulan_db, mulan_session, fetchone=[_with_annotation()])

        r = cookie.get(f'{URL}/{IMAGE_ID}/annotation/package')

        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.data)) as archive:
            assert sorted(archive.namelist()) == ['annotations.json', 'mask.tif']

    def test_annotations_json_round_trips(self, cookie, mulan_db, mulan_session, stored_mask):
        row = _with_annotation()
        _authed(mulan_db, mulan_session, fetchone=[row])

        r = cookie.get(f'{URL}/{IMAGE_ID}/annotation/package')

        with zipfile.ZipFile(io.BytesIO(r.data)) as archive:
            assert json.loads(archive.read('annotations.json')) == row['annotation_metadata']
            assert archive.read('mask.tif') == b'II*\x00mask-bytes'
        stored_mask.get_object.assert_called_once_with('annotations', MASK_KEY)
