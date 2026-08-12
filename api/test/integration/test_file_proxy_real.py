"""Real MinIO integration tests for the file_proxy resource.

Requires the dev MinIO stack running (see docker/docker-compose.yml).
Run with: pytest -m integration
"""
import io

import pytest


pytestmark = pytest.mark.integration


class TestRealRoundTrip:
    def test_get_returns_uploaded_object_bytes(self, real_client, auth_headers, real_minio_client, test_bucket):
        obj_name = 'greeting.txt'
        content = b'hello real minio'
        real_minio_client.put_object(
            test_bucket,
            obj_name,
            io.BytesIO(content),
            length=len(content),
        )

        r = real_client.get(f'/storage/{test_bucket}/{obj_name}', headers=auth_headers)

        assert r.status_code == 200
        assert r.data == content
        assert r.mimetype == 'text/plain'

    def test_get_json_object_sets_json_mimetype(self, real_client, auth_headers, real_minio_client, test_bucket):
        obj_name = 'payload.json'
        content = b'{"hello": "world"}'
        real_minio_client.put_object(
            test_bucket,
            obj_name,
            io.BytesIO(content),
            length=len(content),
        )

        r = real_client.get(f'/storage/{test_bucket}/{obj_name}', headers=auth_headers)

        assert r.status_code == 200
        assert r.mimetype == 'application/json'
        assert r.data == content

    def test_get_nested_path_returns_object_and_uses_last_segment_as_filename(
        self, real_client, auth_headers, real_minio_client, test_bucket
    ):
        obj_name = 'nested/dir/model.glb'
        content = b'\x00glb-bytes'
        real_minio_client.put_object(
            test_bucket,
            obj_name,
            io.BytesIO(content),
            length=len(content),
        )

        r = real_client.get(f'/storage/{test_bucket}/{obj_name}', headers=auth_headers)

        assert r.status_code == 200
        assert r.data == content
        assert r.mimetype == 'model/gltf-binary'
        assert 'model.glb' in r.headers.get('Content-Disposition', '')


class TestRealErrors:
    def test_missing_object_returns_404(self, real_client, auth_headers, test_bucket):
        r = real_client.get(
            f'/storage/{test_bucket}/does-not-exist.txt',
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_missing_bucket_returns_404(self, real_client, auth_headers):
        r = real_client.get(
            '/storage/definitely-not-a-real-bucket-xyz/anything.txt',
            headers=auth_headers,
        )
        assert r.status_code == 404


class TestRealAuth:
    def test_missing_header_still_rejects_without_touching_minio(self, real_client, test_bucket):
        # If auth ran after MinIO fetch, we'd get a 200 or 404 instead of 401.
        r = real_client.get(f'/storage/{test_bucket}/anything.txt')
        assert r.status_code == 401
