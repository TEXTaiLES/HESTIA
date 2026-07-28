"""Real integration for the thumbnail resource.

Hits real Postgres (reads the reconstructions row) and real Directus (uploads a
PNG, patches the artefact.thumbnail field). The pyrender/subprocess rendering
step is mocked because it requires OpenGL/EGL that's not available on Windows.

Run with: pytest -m integration
"""
import pytest
import requests


pytestmark = pytest.mark.integration


def _thumbnail_url(object_id: str) -> str:
    return f'/reconstructions/{object_id}/generate-thumbnail'


class TestRealPipeline:
    def test_happy_path_uploads_png_and_returns_file_id(
        self, real_client_thumbnail, auth_headers, test_reconstruction,
        directus_files_cleanup, directus_config, directus_token,
    ):
        c = real_client_thumbnail['client']

        r = c.post(
            _thumbnail_url(test_reconstruction['object_id']),
            headers=auth_headers,
            json={},
        )

        assert r.status_code == 201
        body = r.get_json()
        assert 'file_id' in body
        directus_files_cleanup.append(body['file_id'])

        # Verify the file actually exists in Directus /files
        resp = requests.get(
            f"{directus_config['url']}/files/{body['file_id']}",
            headers={'Authorization': f'Bearer {directus_token}'},
            timeout=10,
        )
        assert resp.ok
        assert resp.json()['data']['id'] == body['file_id']

    def test_render_receives_stripped_glb_path(
        self, real_client_thumbnail, auth_headers, test_reconstruction, directus_files_cleanup,
    ):
        c = real_client_thumbnail['client']

        r = c.post(
            _thumbnail_url(test_reconstruction['object_id']),
            headers=auth_headers,
            json={},
        )
        directus_files_cleanup.append(r.get_json()['file_id'])

        # glb_location was "s3://reconstructions/<object_id>/model.glb"; render
        # should be called with just "<object_id>/model.glb".
        expected_object_name = f"{test_reconstruction['object_id']}/model.glb"
        real_client_thumbnail['render'].assert_called_once_with(expected_object_name)

    def test_with_artefact_id_links_thumbnail_in_directus(
        self, real_client_thumbnail, auth_headers, test_reconstruction, test_artefact,
        directus_files_cleanup, directus_config, directus_token,
    ):
        c = real_client_thumbnail['client']

        r = c.post(
            _thumbnail_url(test_reconstruction['object_id']),
            headers=auth_headers,
            json={'artefact_id': test_artefact},
        )

        assert r.status_code == 201
        body = r.get_json()
        directus_files_cleanup.append(body['file_id'])

        # Verify artefact.thumbnail was updated in Directus
        resp = requests.get(
            f"{directus_config['url']}/items/artefacts/{test_artefact}",
            headers={'Authorization': f'Bearer {directus_token}'},
            params={'fields': 'thumbnail'},
            timeout=10,
        )
        assert resp.ok
        assert resp.json()['data']['thumbnail'] == body['file_id']


class TestRealErrors:
    def test_nonexistent_reconstruction_returns_404(self, real_client_thumbnail, auth_headers):
        c = real_client_thumbnail['client']
        r = c.post(_thumbnail_url('does-not-exist-xyz'), headers=auth_headers, json={})
        assert r.status_code == 404

    def test_render_failure_returns_500_and_skips_directus(
        self, real_client_thumbnail, auth_headers, test_reconstruction, mocker,
    ):
        real_client_thumbnail['render'].return_value = None
        upload_spy = mocker.spy(__import__('services.directus', fromlist=['upload_file']), 'upload_file')

        c = real_client_thumbnail['client']
        r = c.post(
            _thumbnail_url(test_reconstruction['object_id']),
            headers=auth_headers,
            json={},
        )

        assert r.status_code == 500
        upload_spy.assert_not_called()


class TestRealAuth:
    def test_missing_header_rejects_before_touching_db(
        self, real_client_thumbnail, test_reconstruction,
    ):
        c = real_client_thumbnail['client']
        r = c.post(_thumbnail_url(test_reconstruction['object_id']), json={})
        assert r.status_code == 401
