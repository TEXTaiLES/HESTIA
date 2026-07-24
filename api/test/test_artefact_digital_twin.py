import pytest


ARTEFACT_ID = 'artefact-123'
URL = f'/artefacts/{ARTEFACT_ID}/digital-twin-uri'


class TestAuth:
    def test_get_missing_header_returns_401(self, client):
        r = client.get(URL)
        assert r.status_code == 401

    def test_get_wrong_bearer_returns_401(self, client):
        r = client.get(URL, headers={'Authorization': 'Bearer wrong'})
        assert r.status_code == 401

    def test_patch_missing_header_returns_401(self, client):
        r = client.patch(URL, json={'digital_twin_uri': 'echoes://x'})
        assert r.status_code == 401


class TestGet:
    # Checks that the API returns the correct artefact id and digital twin URI when the upstream service succeeds.
    def test_returns_uri_when_upstream_succeeds(self, client, auth_headers, mocker):
        mocker.patch(
            'resources.artefact_digital_twin.get_artefact_digital_twin_uri',
            return_value='echoes://scene/abc',
        )
        r = client.get(URL, headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json() == {
            'artefact_id': ARTEFACT_ID,
            'digital_twin_uri': 'echoes://scene/abc',
        }

    # Checks that the API calls the upstream service with the correct artefact id when handling a GET request.
    def test_calls_service_with_artefact_id(self, client, auth_headers, mocker):
        spy = mocker.patch(
            'resources.artefact_digital_twin.get_artefact_digital_twin_uri',
            return_value='echoes://x',
        )
        client.get(URL, headers=auth_headers)
        spy.assert_called_once_with(ARTEFACT_ID)

    # Checks that the API returns a 502 error when the upstream service returns a falsy value (None or empty string).
    @pytest.mark.parametrize('upstream_return', [None, ''])
    def test_returns_502_when_upstream_returns_falsy(self, client, auth_headers, mocker, upstream_return):
        mocker.patch(
            'resources.artefact_digital_twin.get_artefact_digital_twin_uri',
            return_value=upstream_return,
        )
        r = client.get(URL, headers=auth_headers)
        assert r.status_code == 502
        assert 'error' in r.get_json()
        assert ARTEFACT_ID in r.get_json()['error']


class TestPatch:
    # Checks that the API successfully updates the digital twin URI when provided with a valid request body and the upstream service succeeds.
    def test_updates_uri_when_body_valid_and_upstream_succeeds(self, client, auth_headers, mocker):
        set_spy = mocker.patch(
            'resources.artefact_digital_twin.set_artefact_digital_twin_uri',
            return_value=True,
        )
        r = client.patch(
            URL,
            headers=auth_headers,
            json={'digital_twin_uri': 'echoes://scene/new'},
        )
        assert r.status_code == 200
        assert r.get_json() == {
            'artefact_id': ARTEFACT_ID,
            'digital_twin_uri': 'echoes://scene/new',
        }
        set_spy.assert_called_once_with(ARTEFACT_ID, 'echoes://scene/new')

    # Checks that the API returns a 400 error when provided with an invalid request body, and that the upstream service is not called in this case.
    @pytest.mark.parametrize(
        'body',
        [
            {},                              # missing key
            {'digital_twin_uri': ''},        # empty string
            {'digital_twin_uri': None},      # null
            {'digital_twin_uri': 123},       # not a string
            {'digital_twin_uri': ['a']},     # list, not a string
        ],
    )
    def test_invalid_body_returns_400_without_calling_upstream(self, client, auth_headers, mocker, body):
        set_spy = mocker.patch(
            'resources.artefact_digital_twin.set_artefact_digital_twin_uri',
        )
        r = client.patch(URL, headers=auth_headers, json=body)
        assert r.status_code == 400
        assert 'error' in r.get_json()
        set_spy.assert_not_called()

    # Checks that the API returns a 400 error when no request body is provided, and that the upstream service is not called in this case.
    def test_no_body_returns_400(self, client, auth_headers, mocker):
        set_spy = mocker.patch(
            'resources.artefact_digital_twin.set_artefact_digital_twin_uri',
        )
        r = client.patch(URL, headers=auth_headers)
        assert r.status_code == 400
        set_spy.assert_not_called()

    # Checks that the API returns a 502 error when the upstream service fails to update the digital twin URI, and that the error message includes the artefact id.
    def test_returns_502_when_upstream_fails(self, client, auth_headers, mocker):
        mocker.patch(
            'resources.artefact_digital_twin.set_artefact_digital_twin_uri',
            return_value=False,
        )
        r = client.patch(
            URL,
            headers=auth_headers,
            json={'digital_twin_uri': 'echoes://x'},
        )
        assert r.status_code == 502
        assert ARTEFACT_ID in r.get_json()['error']