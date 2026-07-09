import logging
import os
from typing import Optional

import requests
from flask import Response, request
from flask_restful import Resource

from middleware.security import require_api_key
from services.directus import (
    get_artefact_digital_twin_uri,
    set_artefact_digital_twin_uri,
)


logger = logging.getLogger(__name__)


# ECHOES Swagger UI: https://echoes-kb-api-route-echoes-graphs-production.apps.dcw1.paas.psnc.pl/swagger-ui/index.html#/


ECHOES_BASE_URL = os.environ.get(
    "ECHOES_BASE_URL",
    "https://echoes-kb-api-route-echoes-graphs-production.apps.dcw1.paas.psnc.pl",
)

ECHOES_REGISTER_ENDPOINT = "/hdt/register"
ECHOES_ENRICH_ENDPOINT   = "/hdt/enrich"
ECHOES_DOWNLOAD_ENDPOINT = "/hdt/download/file"

ECHOES_PROJECT_URI = "http://echoes-eccch.eu/TEXTaiLES"
ECHOES_TRIPLESTORE_ID = "6a2abf6b5d6646ff24522299"

ECHOES_AUTH_TOKEN = os.environ.get("ECHOES_AUTH_TOKEN", "")


def register_digital_twin(artifact_id: str) -> tuple[Optional[str], dict | str]:
    """Register `artifact_id` on ECHOES. Returns (dtUri, raw response data)."""
    response = requests.post(
        f"{ECHOES_BASE_URL}{ECHOES_REGISTER_ENDPOINT}",
        params={
            "heritageEntityUri": artifact_id,
            "projectUri": ECHOES_PROJECT_URI,
            "name": artifact_id,
            "description": artifact_id,
        },
        headers={
            "accept": "application/json",
            "Authorization": f"Bearer {ECHOES_AUTH_TOKEN}",
        },
        timeout=30,
    )

    try:
        data = response.json()
    except ValueError:
        data = response.text

    if not response.ok:
        logger.error(
            "ECHOES registration failed for artifact %s: %s",
            artifact_id,
            response.text,
        )
        return None, data

    dt_uri = data.get("dtUri") if isinstance(data, dict) else None
    return dt_uri, data


def enrich_digital_twin(
    dt_uri: str,
    rdf_content: bytes,
    filename: str = "data.rdf",
) -> tuple[bool, dict | str]:
    """Push RDF content to the ECHOES Digital Twin at `dt_uri`."""
    response = requests.post(
        f"{ECHOES_BASE_URL}{ECHOES_ENRICH_ENDPOINT}",
        data={
            "contentType": "application/rdf+xml",
            "digitalTwinUri": dt_uri,
            "triplestoreId": ECHOES_TRIPLESTORE_ID,
        },
        files={
            "file": (filename, rdf_content, "application/rdf+xml"),
        },
        headers={
            "accept": "application/json",
            "Authorization": f"Bearer {ECHOES_AUTH_TOKEN}",
        },
        timeout=30,
    )

    try:
        data = response.json()
    except ValueError:
        data = response.text

    if not response.ok:
        logger.error("ECHOES enrichment failed for %s: %s", dt_uri, response.text)
        return False, data

    return True, data


class EchoesResource(Resource):
    """Register an artifact as an ECHOES Digital Twin."""

    method_decorators = [require_api_key]

    def get(self, artifact_id: str):
        """Download the ECHOES Digital Twin entry for `artifact_id`."""
        dt_uri = get_artefact_digital_twin_uri(artifact_id)
        if not dt_uri:
            return {"error": "Digital Twin URI not found for artifact"}, 404

        response = requests.post(
            f"{ECHOES_BASE_URL}{ECHOES_DOWNLOAD_ENDPOINT}",
            json={
                "digitalTwinUri": dt_uri,
                "tripleStoreIds": [ECHOES_TRIPLESTORE_ID],
                "format": "application/ld+json",
            },
            headers={
                "accept": "application/octet-stream",
                "Authorization": f"Bearer {ECHOES_AUTH_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

        if not response.ok:
            logger.error(
                "ECHOES download failed for artifact %s: %s",
                artifact_id,
                response.text,
            )
            return {
                "error": "ECHOES download failed",
                "status_code": response.status_code,
                "response": response.text,
            }, 502

        return Response(
            response.content,
            content_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{artifact_id}.jsonld"',
            },
        )

    def post(self, artifact_id: str):
        """Register `artifact_id` with ECHOES and return the resulting dtUri."""
        dt_uri, data = register_digital_twin(artifact_id)
        if not dt_uri:
            return {
                "error": "ECHOES registration failed or did not include dtUri",
                "response": data,
            }, 502

        if not set_artefact_digital_twin_uri(artefact_id=artifact_id, uri=dt_uri):
            return {
                "error": f"Failed to update digital_twin_uri on artefact '{artifact_id}'",
                "dtUri": dt_uri,
            }, 502

        return {
            "message": "ECHOES Digital Twin registered",
            "artifact_id": artifact_id,
            "dtUri": dt_uri,
            "echoes_response": data,
        }, 201

    def put(self, artifact_id: str):
        """Enrich `artifact_id` on ECHOES with RDF content."""
        dt_uri = get_artefact_digital_twin_uri(artifact_id)
        if not dt_uri:
            return {"error": "Digital Twin URI not found for artifact"}, 404

        uploaded_file = request.files.get("file")
        if uploaded_file:
            rdf_content = uploaded_file.read()
            filename = uploaded_file.filename or "data.rdf"
        else:
            rdf_content = request.get_data()
            filename = "data.rdf"

        if not rdf_content:
            return {"error": "No RDF content provided"}, 400

        success, data = enrich_digital_twin(dt_uri, rdf_content, filename)
        if not success:
            return {
                "error": "ECHOES enrichment failed",
                "response": data,
            }, 502

        return {
            "message": "ECHOES Digital Twin enriched",
            "artifact_id": artifact_id,
            "dtUri": dt_uri,
            "echoes_response": data,
        }, 200
