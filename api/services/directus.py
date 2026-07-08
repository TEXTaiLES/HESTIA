import io
import logging
import os

import requests

logger = logging.getLogger(__name__)

DIRECTUS_URL = os.environ.get('DIRECTUS_URL', 'http://directus:8055')
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