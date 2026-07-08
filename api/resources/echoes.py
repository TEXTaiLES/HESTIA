import logging
from typing import Optional

import requests
from flask import Response, request
from flask_restful import Resource

from middleware.security import require_api_key


logger = logging.getLogger(__name__)


# ECHOES Swagger UI: https://echoes-kb-api-route-echoes-graphs-production.apps.dcw1.paas.psnc.pl/swagger-ui/index.html#/


ECHOES_BASE_URL = "https://echoes-kb-api-route-echoes-graphs-production.apps.dcw1.paas.psnc.pl"

ECHOES_REGISTER_ENDPOINT = "/hdt/register"
ECHOES_ENRICH_ENDPOINT   = "/hdt/enrich"
ECHOES_DOWNLOAD_ENDPOINT = "/hdt/download/file"

ECHOES_PROJECT_URI = "http://echoes-eccch.eu/TEXTaiLES"
ECHOES_TRIPLESTORE_ID = "6a2abf6b5d6646ff24522299"

ECHOES_AUTH_TOKEN = ""


def save_dt_uri(artifact_id: str, dt_uri: str) -> None:
    """Placeholder for persisting the Digital Twin URI later."""
    logger.info("Retrieved ECHOES dtUri for artifact %s: %s", artifact_id, dt_uri)


def get_dt_uri(artifact_id: str) -> Optional[str]:
    """Placeholder for retrieving the persisted Digital Twin URI later."""
    return "http://echoes-eccch.eu/HDT/8O9Pe2xznoA"


class EchoesResource(Resource):
    """Register an artifact as an ECHOES Digital Twin."""

    method_decorators = [require_api_key]

    def get(self, artifact_id: str):
        """Download the ECHOES Digital Twin entry for `artifact_id`."""
        dt_uri = get_dt_uri(artifact_id=artifact_id)
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
            data = None

        if not response.ok:
            logger.error(
                "ECHOES registration failed for artifact %s: %s",
                artifact_id,
                response.text,
            )
            return {
                "error": "ECHOES registration failed",
                "status_code": response.status_code,
                "response": data if data is not None else response.text,
            }, 502

        dt_uri: Optional[str] = data.get("dtUri") if isinstance(data, dict) else None
        if not dt_uri:
            return {
                "error": "ECHOES registration response did not include dtUri",
                "response": data if data is not None else response.text,
            }, 502

        save_dt_uri(artifact_id=artifact_id, dt_uri=dt_uri)

        return {
            "message": "ECHOES Digital Twin registered",
            "artifact_id": artifact_id,
            "dtUri": dt_uri,
            "echoes_response": data,
        }, 201

    def put(self, artifact_id: str):
        """Enrich `artifact_id` on ECHOES with RDF content."""
        dt_uri = get_dt_uri(artifact_id=artifact_id)
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
            data = None
        
        if not response.ok:
            logger.error(
                "ECHOES enrichment failed for artifact %s: %s",
                artifact_id,
                response.text,
            )
            return {
                "error": "ECHOES enrichment failed",
                "status_code": response.status_code,
                "response": data if data is not None else response.text,
            }, 502

        return {
            "message": "ECHOES Digital Twin enriched",
            "artifact_id": artifact_id,
            "dtUri": dt_uri,
            "echoes_response": data,
        }, 200
