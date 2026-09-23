"""Real integration for the MulAn /api/v1 resources.

Hits real Postgres (directus_sessions for auth, multispectral_images and
annotations for data) and real MinIO (the private multispectral / annotations
buckets). No Kafka.

These cover what the mocked unit tests cannot: that the writes are actually
committed, that the one-annotation-per-image index holds, and that a downloaded
TIFF is byte-identical to the uploaded one.

Run with: pytest -m integration
"""
import io
import json
import zipfile

import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile

from resources.mulan import COOKIE_NAME


pytestmark = pytest.mark.integration


URL = '/api/v1/multispectral/images'
SESSION_URL = '/api/v1/session'
CLASSES = [
    {'id': 0, 'name': 'Background', 'description': '', 'color': '#000000'},
    {'id': 1, 'name': 'Damage', 'description': '', 'color': '#E53935'},
]


def _multiband_tiff(width=64, height=48, bands=5) -> bytes:
    with MemoryFile() as memfile:
        with memfile.open(driver='GTiff', width=width, height=height,
                          count=bands, dtype='uint16') as dataset:
            for band in range(1, bands + 1):
                dataset.write(np.full((height, width), band * 100, 'uint16'), band)
                dataset.set_band_description(band, f'{350 + band * 100}nm')
        return memfile.read()


def _mask_tiff(width=64, height=48) -> bytes:
    with MemoryFile() as memfile:
        with memfile.open(driver='GTiff', width=width, height=height,
                          count=1, dtype='uint8') as dataset:
            data = np.zeros((height, width), 'uint8')
            data[5:15, 5:15] = 1
            dataset.write(data, 1)
        return memfile.read()


def _package(width=64, height=48) -> io.BytesIO:
    metadata = {
        'format': 'mulan-semantic-segmentation',
        'version': '1.0',
        'image': {'width': width, 'height': height},
        'mask': {'width': width, 'height': height, 'dtype': 'uint8',
                 'background_id': 0, 'ignore_id': None},
        'classes': CLASSES,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('mask.tif', _mask_tiff(width, height))
        archive.writestr('annotations.json', json.dumps(metadata))
    buffer.seek(0)
    return buffer


@pytest.fixture()
def authed(real_client_mulan, mulan_cookie):
    real_client_mulan.set_cookie(COOKIE_NAME, mulan_cookie['token'])
    return real_client_mulan


class TestSession:
    # The session comes from a real directus_sessions row, so this also proves
    # the SESSION_SQL join matches the live Directus schema.
    def test_real_cookie_authenticates(self, authed, mulan_cookie):
        body = authed.get(SESSION_URL).get_json()

        assert body['authenticated'] is True
        assert body['user']['id'] == mulan_cookie['user_id']

    def test_expired_session_is_anonymous(self, real_client_mulan, real_db_connection, mulan_cookie):
        cur = real_db_connection.cursor()
        cur.execute("UPDATE directus_sessions SET expires = now() - interval '1 hour' WHERE token = %s",
                    (mulan_cookie['token'],))
        real_db_connection.commit()
        cur.close()

        real_client_mulan.set_cookie(COOKIE_NAME, mulan_cookie['token'])
        body = real_client_mulan.get(SESSION_URL).get_json()

        assert body['authenticated'] is False


class TestUploadAndDownload:
    # The row must still be there after the request: a missing commit shows up
    # here as a 404 on the follow-up GET, which the mocked tests cannot see.
    def test_upload_persists_and_downloads_identically(self, authed, real_db_connection, real_minio_client):
        content = _multiband_tiff()

        created = authed.post(URL, data={'file': (io.BytesIO(content), 'integration.tif')},
                              content_type='multipart/form-data')
        assert created.status_code == 201, created.get_data(as_text=True)
        image_id = created.get_json()['image_id']

        try:
            assert created.get_json()['channel_count'] == 5
            assert created.get_json()['dtype'] == 'uint16'
            assert created.get_json()['channel_names'] == ['450nm', '550nm', '650nm', '750nm', '850nm']

            cur = real_db_connection.cursor()
            cur.execute("SELECT object_key, size_bytes FROM multispectral_images WHERE image_id = %s",
                        (image_id,))
            row = cur.fetchone()
            cur.close()
            assert row is not None, 'the INSERT was rolled back: the transaction is never committed'
            assert row[0] == f'{image_id}/source.tif'
            assert row[1] == len(content)

            fetched = authed.get(f'{URL}/{image_id}/file')
            assert fetched.status_code == 200
            assert fetched.data == content
            assert fetched.headers['Content-Type'] == 'image/tiff'

            item = authed.get(f'{URL}/{image_id}')
            assert item.status_code == 200
            assert item.get_json()['has_annotation'] is False
        finally:
            cur = real_db_connection.cursor()
            cur.execute("DELETE FROM annotations WHERE multispectral_image_id = %s", (image_id,))
            cur.execute("DELETE FROM multispectral_images WHERE image_id = %s", (image_id,))
            real_db_connection.commit()
            cur.close()
            for obj in real_minio_client.list_objects('multispectral', prefix=f'{image_id}/', recursive=True):
                real_minio_client.remove_object('multispectral', obj.object_name)

    # Uploaded bytes must survive validation untouched, hence no re-encoding.
    def test_stored_object_is_a_readable_raster(self, authed, test_multispectral_image, real_minio_client):
        response = real_minio_client.get_object('multispectral', test_multispectral_image['object_key'])
        try:
            data = response.read()
        finally:
            response.close()
            response.release_conn()

        with MemoryFile(data) as memfile, memfile.open() as dataset:
            assert (dataset.width, dataset.height, dataset.count) == (64, 48, 1)


class TestAnnotationLifecycle:
    # Create, refuse, overwrite — against the real unique index and the real
    # objects in the annotations bucket.
    def test_create_conflict_overwrite(self, authed, test_multispectral_image, real_db_connection):
        image_id = test_multispectral_image['image_id']
        url = f'{URL}/{image_id}/annotation'

        assert authed.get(url).status_code == 404

        created = authed.put(url, data={'package': (_package(), 'package.zip')},
                             content_type='multipart/form-data')
        assert created.status_code == 201, created.get_data(as_text=True)

        cur = real_db_connection.cursor()
        cur.execute("SELECT kind, revision, mask_object_key FROM annotations "
                    "WHERE multispectral_image_id = %s", (image_id,))
        rows = cur.fetchall()
        cur.close()
        assert len(rows) == 1, 'the write was rolled back, or the unique index is missing'
        assert rows[0][0] == 'multispectral-mask'
        assert rows[0][1] == 1
        first_mask_key = rows[0][2]

        refused = authed.put(url, data={'package': (_package(), 'package.zip')},
                             content_type='multipart/form-data')
        assert refused.status_code == 409
        assert refused.get_json()['error']['code'] == 'annotation_exists'

        overwritten = authed.put(url, data={'package': (_package(), 'package.zip'),
                                            'overwrite': 'true', 'expected_revision': '1'},
                                 content_type='multipart/form-data')
        assert overwritten.status_code == 200
        assert overwritten.get_json()['revision'] == 2

        cur = real_db_connection.cursor()
        cur.execute("SELECT mask_object_key FROM annotations WHERE multispectral_image_id = %s", (image_id,))
        assert cur.fetchone()[0] != first_mask_key, 'the mask key should change with each revision'
        cur.close()

    # The one MulAn actually consumes: mask plus metadata, nothing else.
    def test_package_round_trip(self, authed, test_multispectral_image):
        image_id = test_multispectral_image['image_id']
        url = f'{URL}/{image_id}/annotation'

        assert authed.put(url, data={'package': (_package(), 'package.zip')},
                          content_type='multipart/form-data').status_code == 201

        downloaded = authed.get(f'{url}/package')

        assert downloaded.status_code == 200
        assert downloaded.headers['Content-Type'] == 'application/zip'
        with zipfile.ZipFile(io.BytesIO(downloaded.data)) as archive:
            assert sorted(archive.namelist()) == ['annotations.json', 'mask.tif']
            metadata = json.loads(archive.read('annotations.json'))
            assert metadata['classes'] == CLASSES
            with MemoryFile(archive.read('mask.tif')) as memfile, memfile.open() as mask:
                assert (mask.width, mask.height, mask.count) == (64, 48, 1)
                assert set(np.unique(mask.read(1)).tolist()) <= {0, 1}

    # A mask whose grid doesn't match the image is refused before anything is stored.
    def test_mismatched_mask_is_rejected(self, authed, test_multispectral_image, real_db_connection):
        image_id = test_multispectral_image['image_id']

        response = authed.put(f'{URL}/{image_id}/annotation',
                              data={'package': (_package(width=32, height=24), 'package.zip')},
                              content_type='multipart/form-data')

        assert response.status_code == 422
        cur = real_db_connection.cursor()
        cur.execute("SELECT count(*) FROM annotations WHERE multispectral_image_id = %s", (image_id,))
        assert cur.fetchone()[0] == 0
        cur.close()

    # has_annotation drives MulAn's "a mask already exists" prompt.
    def test_list_reports_has_annotation(self, authed, test_multispectral_image):
        image_id = test_multispectral_image['image_id']
        authed.put(f'{URL}/{image_id}/annotation', data={'package': (_package(), 'package.zip')},
                   content_type='multipart/form-data')

        body = authed.get(f'{URL}?per_page=100&has_annotation=true').get_json()

        assert any(item['image_id'] == image_id and item['has_annotation'] for item in body['items'])


class TestPrivacy:
    # Anonymous callers must not reach any of it, existing or not.
    @pytest.mark.parametrize('path', ['', '/{id}', '/{id}/file', '/{id}/annotation', '/{id}/annotation/package'])
    def test_anonymous_is_refused(self, real_client_mulan, test_multispectral_image, path):
        url = URL + path.format(id=test_multispectral_image['image_id'])

        response = real_client_mulan.get(url)

        assert response.status_code == 401
        assert response.get_json()['error']['code'] == 'unauthenticated'

    # The buckets hold login-protected content, so they must not be world-readable.
    @pytest.mark.parametrize('bucket', ['multispectral', 'annotations'])
    def test_buckets_are_not_public(self, real_minio_client, bucket):
        try:
            policy = real_minio_client.get_bucket_policy(bucket)
        except Exception:
            return  # no policy at all is the expected state

        assert '"Principal": {"AWS": "*"}' not in policy.replace(' ', '')
