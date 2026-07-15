import logging
import os
from pathlib import Path
from typing import Optional

import requests
from flask import Response, request
from flask_restful import Resource

from middleware.security import require_api_key
from scripts.convert_schema import textailes_json_to_xml, textailes_to_hdto
from services.directus import (
    get_artefact_digital_twin_uri,
    set_artefact_digital_twin_uri,
)


logger = logging.getLogger(__name__)


# ECHOES Swagger UI: https://echoes-kb-api-route-echoes-graphs-production.apps.dcw1.paas.psnc.pl/swagger-ui/index.html#/


ECHOES_BASE_URL = "https://echoes-kb-api-route-echoes-graphs-production.apps.dcw1.paas.psnc.pl"

ECHOES_REGISTER_ENDPOINT = "/hdt/register"
ECHOES_ENRICH_ENDPOINT   = "/hdt/enrich"
ECHOES_DOWNLOAD_ENDPOINT = "/hdt/download/file"

ECHOES_HERITAGE_ENTITY_BASE_URL = "https://textailes.athenarc.gr/archive/artefacts/"
ECHOES_PROJECT_URI = "https://textailes-eccch.eu/"
ECHOES_TRIPLESTORE_ID = os.environ.get('ECHOES_TRIPLESTORE_ID')

# Generate an access token using the refresh token.
ECHOES_AUTH_TOKEN = ""
ECHOES_REFRESH_TOKEN = os.environ.get('ECHOES_REFRESH_TOKEN')
if ECHOES_REFRESH_TOKEN:
    token_response = requests.post(
        "https://aai-demo.egi.eu/auth/realms/egi/protocol/openid-connect/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": ECHOES_REFRESH_TOKEN,
            "client_id": "token-portal",
            "scope": "openid email profile voperson_id voperson_external_affiliation entitlements eduperson_entitlement"
        },
    )
    if not token_response.ok:
        logger.error("ECHOES token refresh failed: %s", token_response.text)
    else:
        ECHOES_AUTH_TOKEN = token_response.json().get('access_token')

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
        response = requests.post(
            f"{ECHOES_BASE_URL}{ECHOES_REGISTER_ENDPOINT}",
            params={
                "heritageEntityUri": f"{ECHOES_HERITAGE_ENTITY_BASE_URL}{artifact_id}", # TODO: Ask CH partners about it.
                "projectUri": ECHOES_PROJECT_URI, # TODO: Check with ECHOES about this value.
                "name": "Sample TEXTaiLES artefact name", # TODO: Use artefact Title.
                "description": "Sample TEXTaiLES artefact description", # TODO: Use artefact Description.
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
            raw_content = uploaded_file.read()
            filename = uploaded_file.filename or "data.rdf"
        else:
            raw_content = request.get_data()
            filename = "data.rdf"

        if not raw_content:
            return {"error": "No content provided"}, 400

        if raw_content.lstrip()[:1] in (b"{", b"["):
            try:
                json_content = raw_content.decode("utf-8").replace('"-"', '""')
                textailes_xml = textailes_json_to_xml(json_content)
                rdf_content = textailes_to_hdto(textailes_xml).encode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                logger.error(
                    "Failed to convert JSON to HDTO RDF for artifact %s: %s",
                    artifact_id,
                    exc,
                )
                return {"error": f"Failed to convert JSON to HDTO RDF: {exc}"}, 400
            filename = f"{Path(filename).stem}.rdf"
        else:
            rdf_content = raw_content

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
