from flask import request
from flask_restful import Resource
import logging

from middleware.security import require_api_key
from services.directus import (
    get_artefact_digital_twin_uri,
    set_artefact_digital_twin_uri,
)

logger = logging.getLogger(__name__)


class ArtefactDigitalTwinUriResource(Resource):
    method_decorators = [require_api_key]

    def get(self, artefact_id):
        """Get the digital_twin_uri field from a Directus artefact."""
        uri = get_artefact_digital_twin_uri(artefact_id)
        if not uri:
            return {'error': f"Failed to get digital_twin_uri on artefact '{artefact_id}'"}, 502

        return {'artefact_id': artefact_id, 'digital_twin_uri': uri}, 200

    def patch(self, artefact_id):
        """
        Set the digital_twin_uri field on a Directus artefact.

        Body (JSON):
          digital_twin_uri: str  — the Echoes URI returned after uploading the artefact
        """
        body = request.get_json(silent=True) or {}
        uri = body.get('digital_twin_uri')
        if not uri or not isinstance(uri, str):
            return {'error': "Missing or invalid 'digital_twin_uri' (expected non-empty string)"}, 400

        if not set_artefact_digital_twin_uri(artefact_id, uri):
            return {'error': f"Failed to update digital_twin_uri on artefact '{artefact_id}'"}, 502

        return {'artefact_id': artefact_id, 'digital_twin_uri': uri}, 200
