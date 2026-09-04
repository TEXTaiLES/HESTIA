import pytest


RESOURCE_MODULE = 'resources.thumbnail'

OBJECT_ID = 'recon-abc'
ARTEFACT_ID = 'art-123'
URL = f'/reconstructions/{OBJECT_ID}/generate-thumbnail'

GLB_OBJECT_NAME = 'sub/dir/model.glb'
GLB_LOCATION = f's3://reconstructions/{GLB_OBJECT_NAME}'  # matches MINIO_RECONSTRUCTION_BUCKET
# The resource reads the row by column name (fetch_one_dict), so the mocked
# cursor has to carry a description just like a real psycopg2 one.
ROW_DESC = [('glb_location',)]
PNG_BYTES = b'\x89PNG\r\n\x1a\nfake'
DIRECTUS_FILE_ID = 'file-xyz'


@pytest.fixture()
def happy_pipeline(mocker, mock_db):
    """Wire the whole pipeline for a successful run; individual tests can override any step."""
    # Database returns a row with a valid GLB location.
    conn, cur = mock_db(fetchone=(GLB_LOCATION,), description=ROW_DESC)
    render = mocker.patch(
        'resources.thumbnail.render_glb_thumbnail',
        return_value=PNG_BYTES,
    )
    upload = mocker.patch(
        'resources.thumbnail.upload_file',
        return_value=DIRECTUS_FILE_ID,
    )
    set_thumb = mocker.patch(
        'resources.thumbnail.set_artefact_thumbnail',
        return_value=True,
    )
    # Recturn all the mocks so that individual tests can inspect calls and override return values.
    return {
        'conn': conn,
        'cur': cur,
        'render': render,
        'upload': upload,
        'set_thumb': set_thumb,
    }


class TestAuth:
    def test_missing_header_returns_401(self, client):
        r = client.post(URL, json={})
        assert r.status_code == 401


class TestDbLookup:
    # Stimulate a database connection error and verify that the API returns a 500 status code with an appropriate error message.
     # Also, ensure that subsequent steps in the pipeline (rendering, uploading, setting thumbnail) are not called.
    def test_returns_500_when_db_raises(self, client, auth_headers, mocker):
        mocker.patch(
            'resources.thumbnail.get_db_connection',
            side_effect=Exception('connection refused'),
        )
        render = mocker.patch('resources.thumbnail.render_glb_thumbnail')
        upload = mocker.patch('resources.thumbnail.upload_file')
        set_thumb = mocker.patch('resources.thumbnail.set_artefact_thumbnail')

        r = client.post(URL, headers=auth_headers, json={})

        assert r.status_code == 500
        assert r.get_json() == {'error': 'Database error'}
        render.assert_not_called()
        upload.assert_not_called()
        set_thumb.assert_not_called()

    # Stimulate a sucesful database connection but no reconstruction.
    def test_returns_404_when_no_row(self, client, auth_headers, mock_db, mocker):
        mock_db(fetchone=None)
        render = mocker.patch('resources.thumbnail.render_glb_thumbnail')
        upload = mocker.patch('resources.thumbnail.upload_file')
        set_thumb = mocker.patch('resources.thumbnail.set_artefact_thumbnail')

        r = client.post(URL, headers=auth_headers, json={})

        assert r.status_code == 404
        assert OBJECT_ID in r.get_json()['error']
        render.assert_not_called()
        upload.assert_not_called()
        set_thumb.assert_not_called()

    # Checks two cases where the GLB location is either None or an empty string, but the reconstruction row exists.
    @pytest.mark.parametrize('glb_location', [None, ''])
    def test_returns_400_when_glb_location_empty(self, client, auth_headers, mock_db, mocker, glb_location):
        mock_db(fetchone=(glb_location,), description=ROW_DESC)
        render = mocker.patch('resources.thumbnail.render_glb_thumbnail')
        upload = mocker.patch('resources.thumbnail.upload_file')
        set_thumb = mocker.patch('resources.thumbnail.set_artefact_thumbnail')

        r = client.post(URL, headers=auth_headers, json={})

        assert r.status_code == 400
        assert r.get_json() == {'error': 'No GLB file exists for this reconstruction yet'}
        render.assert_not_called()
        upload.assert_not_called()
        set_thumb.assert_not_called()

    # Checks that the SQL query is called with the correct object_id parameter when generating a thumbnail.
    def test_sql_called_with_object_id(self, client, auth_headers, happy_pipeline):
        client.post(URL, headers=auth_headers, json={})
        happy_pipeline['cur'].execute.assert_called_once_with(
            "SELECT glb_location FROM reconstructions WHERE object_id = %s",
            (OBJECT_ID,),
        )

    # Checks that the database connection and cursor are properly closed after generating a thumbnail.
    def test_db_connection_closed(self, client, auth_headers, happy_pipeline):
        client.post(URL, headers=auth_headers, json={})
        happy_pipeline['cur'].close.assert_called_once()
        happy_pipeline['conn'].close.assert_called_once()


class TestRendering:
    # Checks that the render function is called with the correct GLB object name.
    def test_render_receives_prefix_stripped_object_name(self, client, auth_headers, happy_pipeline):
        client.post(URL, headers=auth_headers, json={})
        happy_pipeline['render'].assert_called_once_with(GLB_OBJECT_NAME)

    # Checks the unsuccessful rendering cases where the render function returns None or empty bytes.
    @pytest.mark.parametrize('render_return', [None, b''])
    def test_returns_500_when_render_fails(self, client, auth_headers, happy_pipeline, render_return):
        happy_pipeline['render'].return_value = render_return

        r = client.post(URL, headers=auth_headers, json={})

        assert r.status_code == 500
        assert r.get_json() == {'error': 'Thumbnail rendering failed'}
        happy_pipeline['upload'].assert_not_called()
        happy_pipeline['set_thumb'].assert_not_called()


class TestUpload:
    # Checks that the upload function is called with the correct PNG bytes and a derived filename based on the object ID.
    def test_upload_receives_png_bytes_and_derived_filename(self, client, auth_headers, happy_pipeline):
        client.post(URL, headers=auth_headers, json={})
        happy_pipeline['upload'].assert_called_once_with(PNG_BYTES, f'{OBJECT_ID}_thumbnail.png')

    # Stimulates an unsuccessful upload by having the upload function return None.
    def test_returns_502_when_upload_fails(self, client, auth_headers, happy_pipeline):
        happy_pipeline['upload'].return_value = None

        r = client.post(URL, headers=auth_headers, json={})

        assert r.status_code == 502
        assert r.get_json() == {'error': 'Failed to upload thumbnail to Directus'}
        happy_pipeline['set_thumb'].assert_not_called()


class TestArtefactLink:
    # Checks that when no artefact_id is provided in the request, the set_artefact_thumbnail function is not called, and the API returns a 201 status code with the file_id in the response.
    def test_success_without_artefact_id_does_not_call_set_thumbnail(self, client, auth_headers, happy_pipeline):
        r = client.post(URL, headers=auth_headers, json={})

        assert r.status_code == 201
        assert r.get_json() == {'file_id': DIRECTUS_FILE_ID}
        happy_pipeline['set_thumb'].assert_not_called()

    # Checks that when an artefact_id is provided in the request, the set_artefact_thumbnail function is called with the correct parameters, and the API returns a 201 status code with the file_id in the response.
    def test_success_with_artefact_id_links_thumbnail(self, client, auth_headers, happy_pipeline):
        r = client.post(URL, headers=auth_headers, json={'artefact_id': ARTEFACT_ID})

        assert r.status_code == 201
        assert r.get_json() == {'file_id': DIRECTUS_FILE_ID}
        happy_pipeline['set_thumb'].assert_called_once_with(ARTEFACT_ID, DIRECTUS_FILE_ID)

    # Checks that when the set_artefact_thumbnail function fails (returns False), the API returns a 207 status code with a warning message in the response.
    def test_returns_207_with_warning_when_artefact_link_fails(self, client, auth_headers, happy_pipeline):
        happy_pipeline['set_thumb'].return_value = False

        r = client.post(URL, headers=auth_headers, json={'artefact_id': ARTEFACT_ID})

        assert r.status_code == 207
        body = r.get_json()
        assert body['file_id'] == DIRECTUS_FILE_ID
        assert 'warning' in body
        assert ARTEFACT_ID in body['warning']

    # Checks that when the request has no JSON body, the API still generates a thumbnail and returns a 201 status code, and that the set_artefact_thumbnail function is not called.
    def test_no_body_still_succeeds(self, client, auth_headers, happy_pipeline):
        """Body is optional — request with no JSON should still generate a thumbnail."""
        r = client.post(URL, headers=auth_headers)

        assert r.status_code == 201
        happy_pipeline['set_thumb'].assert_not_called()
