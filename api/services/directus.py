import io
import logging
import os

import requests

logger = logging.getLogger(__name__)

DIRECTUS_URL = os.environ.get('DIRECTUS_URL', 'http://directus:8055')
# Public-facing base URL used when handing asset links to external tools (THOTH).
DIRECTUS_PUBLIC_URL = os.environ.get('DIRECTUS_PUBLIC_URL', DIRECTUS_URL)
DIRECTUS_ADMIN_EMAIL = os.environ.get('DIRECTUS_ADMIN_EMAIL')
DIRECTUS_ADMIN_PASSWORD = os.environ.get('DIRECTUS_ADMIN_PASSWORD')


def _get_token() -> str | None:
    try:
        resp = requests.post(
            f"{DIRECTUS_URL}/auth/login",
            json={"email": DIRECTUS_ADMIN_EMAIL, "password": DIRECTUS_ADMIN_PASSWORD},
            timeout=10
        )
        if resp.ok:
            return resp.json()['data']['access_token']
        logger.error(f"Directus auth failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Directus auth error: {e}")
    return None


def upload_file(png_bytes: bytes, filename: str) -> str | None:
    """Upload a PNG to Directus Files. Returns the Directus file ID or None."""
    token = _get_token()
    if not token:
        return None
    try:
        resp = requests.post(
            f"{DIRECTUS_URL}/files",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (filename, io.BytesIO(png_bytes), "image/png")},
            timeout=30
        )
        if resp.ok:
            return resp.json()['data']['id']
        logger.error(f"Directus file upload failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Directus file upload error: {e}")
    return None


def set_artefact_thumbnail(artefact_id: str, file_id: str, collection: str = 'artefacts') -> bool:
    """Set the thumbnail field on a Directus artefact item."""
    token = _get_token()
    if not token:
        return False
    try:
        resp = requests.patch(
            f"{DIRECTUS_URL}/items/{collection}/{artefact_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"thumbnail": file_id},
            timeout=10
        )
        if resp.ok:
            return True
        logger.error(f"Directus artefact update failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Directus artefact update error: {e}")
    return False


def set_artefact_digital_twin_uri(artefact_id: str, uri: str, collection: str = 'artefacts') -> bool:
    """Set the digital_twin_uri field on a Directus artefact item."""
    token = _get_token()
    if not token:
        return False
    try:
        resp = requests.patch(
            f"{DIRECTUS_URL}/items/{collection}/{artefact_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"digital_twin_uri": uri},
            timeout=10
        )
        if resp.ok:
            return True
        logger.error(f"Directus artefact update failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Directus artefact update error: {e}")
    return False


def get_asset_url(file_id: str) -> str:
    """Return the public Directus asset URL for a file id."""
    return f"{DIRECTUS_PUBLIC_URL}/assets/{file_id}"


def get_artefact_item(
    artefact_id: str,
    fields: str = '*,gltf_file.directus_files_id.*',
    collection: str = 'artefacts',
) -> dict | None:
    """Get a full Directus artefact item (including related files)."""
    token = _get_token()
    if not token:
        return None
    try:
        resp = requests.get(
            f"{DIRECTUS_URL}/items/{collection}/{artefact_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": fields},
            timeout=10
        )
        if resp.ok:
            return resp.json().get('data')
        if resp.status_code != 403 and resp.status_code != 404:
            logger.error(f"Directus artefact fetch failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Directus artefact fetch error: {e}")
    return None


def update_artefact_item(artefact_id: str, payload: dict, collection: str = 'artefacts') -> bool:
    """Patch arbitrary fields on a Directus artefact item."""
    token = _get_token()
    if not token:
        return False
    try:
        resp = requests.patch(
            f"{DIRECTUS_URL}/items/{collection}/{artefact_id}",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=10
        )
        if resp.ok:
            return True
        logger.error(f"Directus artefact update failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Directus artefact update error: {e}")
    return False


def ensure_json_fields(field_names: tuple[str, ...], collection: str = 'artefacts') -> bool:
    """Create missing JSON fields on a Directus collection.

    Metadata pushed from THOTH lands in JSON fields (ch_metadata, annotations)
    that older deployments don't have yet; this creates them on demand.
    """
    token = _get_token()
    if not token:
        return False
    try:
        resp = requests.get(
            f"{DIRECTUS_URL}/fields/{collection}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        if not resp.ok:
            logger.error(f"Directus fields fetch failed ({resp.status_code}): {resp.text}")
            return False
        existing = {f.get('field') for f in resp.json().get('data', [])}

        for field_name in field_names:
            if field_name in existing:
                continue
            create_resp = requests.post(
                f"{DIRECTUS_URL}/fields/{collection}",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "field": field_name,
                    "type": "json",
                    "meta": {"interface": "input-code", "special": ["cast-json"]},
                    "schema": {},
                },
                timeout=10
            )
            if not create_resp.ok:
                logger.error(
                    f"Directus field create '{field_name}' failed "
                    f"({create_resp.status_code}): {create_resp.text}"
                )
                return False
            logger.info(f"Directus: created field '{collection}.{field_name}'")
        return True
    except Exception as e:
        logger.error(f"Directus ensure fields error: {e}")
        return False


def get_artefact_digital_twin_uri(artefact_id: str, collection: str = 'artefacts') -> str | None:
    """Get the digital_twin_uri field from a Directus artefact item."""
    token = _get_token()
    if not token:
        return None
    try:
        resp = requests.get(
            f"{DIRECTUS_URL}/items/{collection}/{artefact_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "digital_twin_uri"},
            timeout=10
        )
        if resp.ok:
            return resp.json().get('data', {}).get('digital_twin_uri')
        logger.error(f"Directus artefact fetch failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Directus artefact fetch error: {e}")
    return None
