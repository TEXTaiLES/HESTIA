"""Real Directus integration tests for the artefact_digital_twin resource.

Requires the dev Directus stack running (docker/docker-compose.yml).
Run with: pytest -m integration
"""
import pytest
import requests


pytestmark = pytest.mark.integration


NONEXISTENT_ID = '00000000-0000-0000-0000-000000000000'


class TestRealRoundTrip:
    def test_patch_then_get_returns_persisted_uri(self, real_client_directus, auth_headers, test_artefact):
        uri = 'echoes://scene/integration-roundtrip'
        # Flask URL path params are always strings, so the response echoes the id as a string
        # even though Directus stores int IDs for the artefacts collection.
        artefact_id_str = str(test_artefact)

        patch_resp = real_client_directus.patch(
            f'/artefacts/{test_artefact}/digital-twin-uri',
            headers=auth_headers,
            json={'digital_twin_uri': uri},
        )
        get_resp = real_client_directus.get(
            f'/artefacts/{test_artefact}/digital-twin-uri',
            headers=auth_headers,
        )

        assert patch_resp.status_code == 200
        assert patch_resp.get_json() == {'artefact_id': artefact_id_str, 'digital_twin_uri': uri}
        assert get_resp.status_code == 200
        assert get_resp.get_json() == {'artefact_id': artefact_id_str, 'digital_twin_uri': uri}

    def test_patch_overwrites_previous_uri(self, real_client_directus, auth_headers, test_artefact):
        first = 'echoes://first'
        second = 'echoes://second'

        real_client_directus.patch(
            f'/artefacts/{test_artefact}/digital-twin-uri',
            headers=auth_headers,
            json={'digital_twin_uri': first},
        )
        real_client_directus.patch(
            f'/artefacts/{test_artefact}/digital-twin-uri',
            headers=auth_headers,
            json={'digital_twin_uri': second},
        )
        get_resp = real_client_directus.get(
            f'/artefacts/{test_artefact}/digital-twin-uri',
            headers=auth_headers,
        )

        assert get_resp.status_code == 200
        assert get_resp.get_json()['digital_twin_uri'] == second

    # Proves the value made it into Directus itself (not just cached by our layer).
    def test_patch_persists_in_directus(
        self, real_client_directus, auth_headers, test_artefact, directus_config, directus_token
    ):
        uri = 'echoes://direct-verify'

        real_client_directus.patch(
            f'/artefacts/{test_artefact}/digital-twin-uri',
            headers=auth_headers,
            json={'digital_twin_uri': uri},
        )

        resp = requests.get(
            f"{directus_config['url']}/items/artefacts/{test_artefact}",
            headers={'Authorization': f'Bearer {directus_token}'},
            params={'fields': 'digital_twin_uri'},
            timeout=10,
        )
        assert resp.ok
        assert resp.json()['data']['digital_twin_uri'] == uri


class TestRealErrors:
    # A freshly created artefact has no digital_twin_uri; the resource treats
    # a falsy upstream value as a 502.
    def test_get_new_artefact_without_uri_returns_502(self, real_client_directus, auth_headers, test_artefact):
        resp = real_client_directus.get(
            f'/artefacts/{test_artefact}/digital-twin-uri',
            headers=auth_headers,
        )
        assert resp.status_code == 502

    def test_get_nonexistent_artefact_returns_502(self, real_client_directus, auth_headers):
        resp = real_client_directus.get(
            f'/artefacts/{NONEXISTENT_ID}/digital-twin-uri',
            headers=auth_headers,
        )
        assert resp.status_code == 502

    def test_patch_nonexistent_artefact_returns_502(self, real_client_directus, auth_headers):
        resp = real_client_directus.patch(
            f'/artefacts/{NONEXISTENT_ID}/digital-twin-uri',
            headers=auth_headers,
            json={'digital_twin_uri': 'echoes://x'},
        )
        assert resp.status_code == 502

    # Body validation short-circuits before Directus is even contacted.
    def test_patch_invalid_body_returns_400(self, real_client_directus, auth_headers, test_artefact):
        resp = real_client_directus.patch(
            f'/artefacts/{test_artefact}/digital-twin-uri',
            headers=auth_headers,
            json={},
        )
        assert resp.status_code == 400


class TestRealAuth:
    def test_missing_header_rejects_before_touching_directus(self, real_client_directus, test_artefact):
        resp = real_client_directus.get(f'/artefacts/{test_artefact}/digital-twin-uri')
        assert resp.status_code == 401
