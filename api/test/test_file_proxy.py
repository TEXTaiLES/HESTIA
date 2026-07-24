import pytest

# Fake MinIO response object to simulate the behavior of the MinIO client during testing.
class FakeMinioResponse:
    def __init__(self, data: bytes):
        self._data = data
        self.closed = False
        self.released = False

    def read(self):
        return self._data

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


@pytest.fixture()
def fake_minio_object(mocker):
    fake = FakeMinioResponse(b'file-bytes')
    # Patch the `get_object` method of the `minio_client` to return a fake response for testing purposes.
    mocker.patch(
        'resources.file_proxy.minio_client.get_object',
        return_value=fake,
    )
    return fake


class TestAuth:
    # No authentication header should return a 401 Unauthorized response.
    def test_missing_header_returns_401(self, client):
        r = client.get('/storage/bucket/some/object.jpg')
        assert r.status_code == 401

    # Wrong bearer token should return a 401 Unauthorized response.
    def test_wrong_bearer_returns_401(self, client):
        r = client.get(
            '/storage/bucket/some/object.jpg',
            headers={'Authorization': 'Bearer wrong'},
        )
        assert r.status_code == 401

    # Missing "Bearer" prefix in the authorization header should return a 401 Unauthorized response.
    def test_missing_bearer_prefix_returns_401(self, client, api_key):
        r = client.get(
            '/storage/bucket/some/object.jpg',
            headers={'Authorization': api_key},
        )
        assert r.status_code == 401

    # Valid bearer token should return a 200 OK response.
    def test_valid_bearer_returns_200(self, client, auth_headers, fake_minio_object):
        r = client.get('/storage/bucket/some/object.jpg', headers=auth_headers)
        assert r.status_code == 200

# One test function in many test cases using pytest's parametrize decorator.
@pytest.mark.parametrize(
    'object_name, expected_mimetype',
    [
        ('img.jpg', 'image/jpeg'),
        ('IMG.JPEG', 'image/jpeg'),
        ('pic.png', 'image/png'),
        ('scan.tif', 'image/tiff'),
        ('scan.tiff', 'image/tiff'),
        ('img.bmp', 'image/bmp'),
        ('data.json', 'application/json'),
        ('note.txt', 'text/plain'),
        ('model.glb', 'model/gltf-binary'),
        ('model.gltf', 'model/gltf+json'),
        ('mat.mtl', 'model/mtl'),
        ('mesh.obj', 'model/obj'),
        ('archive.zip', 'application/octet-stream'),
        ('no-extension', 'application/octet-stream'),
    ],
)
# Checks that the correct MIME type is set depending on object names endings.
# It also verifies that the response data matches the expected file bytes.
def test_success_sets_mimetype(client, auth_headers, fake_minio_object, object_name, expected_mimetype):
    r = client.get(f'/storage/bucket/{object_name}', headers=auth_headers)
    assert r.status_code == 200
    assert r.mimetype == expected_mimetype
    assert r.data == b'file-bytes'

# Checks that the `get_object` method of the MinIO client is called with the correct bucket and key parameters. 
def test_get_object_called_with_bucket_and_key(client, auth_headers, mocker):
    fake = FakeMinioResponse(b'x')
    spy = mocker.patch(
        'resources.file_proxy.minio_client.get_object',
        return_value=fake,
    )
    client.get('/storage/my-bucket/nested/dir/file.png', headers=auth_headers)
    spy.assert_called_once_with('my-bucket', 'nested/dir/file.png')


# Checks that the `Content-Disposition` header contains the correct filename, which should be the last segment of the requested path.
def test_download_name_is_last_path_segment(client, auth_headers, fake_minio_object):
    r = client.get('/storage/bucket/nested/dir/file.png', headers=auth_headers)
    assert r.status_code == 200
    assert 'file.png' in r.headers.get('Content-Disposition', '')

# Checks that MinIO raises an exception when the requested object is not found, and that the API returns a 404.
def test_minio_error_returns_404(client, auth_headers, mocker):
    mocker.patch(
        'resources.file_proxy.minio_client.get_object',
        side_effect=Exception('not found'),
    )
    r = client.get('/storage/bucket/missing.png', headers=auth_headers)
    assert r.status_code == 404
    assert r.get_json() == {'error': 'File not found or access denied.'}

# Checks that the MinIO connection is properly closed and released after a successful request.
def test_connection_released_on_success(client, auth_headers, fake_minio_object):
    client.get('/storage/bucket/img.jpg', headers=auth_headers)
    assert fake_minio_object.closed is True
    assert fake_minio_object.released is True

# Fake MinIO response class that simulates a read failure when attempting to read the object data.
class FailingReadMinioResponse(FakeMinioResponse):
    def read(self):
        raise Exception('read failed')

# Checks that the MinIO connection is properly closed and released even when a read failure occurs during the request.
def test_connection_released_when_read_fails(client, auth_headers, mocker):
    fake = FailingReadMinioResponse(b'')
    mocker.patch(
        'resources.file_proxy.minio_client.get_object',
        return_value=fake,
    )

    r = client.get('/storage/bucket/broken.jpg', headers=auth_headers)

    assert r.status_code == 404
    assert fake.closed is True
    assert fake.released is True