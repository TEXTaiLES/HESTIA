from flask import request
from flask_restful import Resource
import logging

from middleware.security import require_api_key
from services.database import get_db_connection
from services.storage import MINIO_RECONSTRUCTION_BUCKET
from services.thumbnail import render_glb_thumbnail
from services.directus import upload_file, set_artefact_thumbnail

logger = logging.getLogger(__name__)


class ThumbnailResource(Resource):
    method_decorators = [require_api_key]

    def post(self, object_id):
        """
        Generate a thumbnail for a reconstruction and push it to Directus.

        Body (JSON, optional):
          artefact_id: str  — if provided, also sets the thumbnail field on the artefact
        """
        body = request.get_json(silent=True) or {}
        artefact_id = body.get('artefact_id')

        # 1. Look up the GLB location in the DB
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT glb_location FROM reconstructions WHERE object_id = %s",
                (object_id,)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"DB lookup failed for {object_id}: {e}")
            return {'error': 'Database error'}, 500

        if not row:
            return {'error': f"Reconstruction '{object_id}' not found"}, 404

        glb_location = row[0]
        if not glb_location:
            return {'error': 'No GLB file exists for this reconstruction yet'}, 400

        # glb_location is "s3://<bucket>/<object_name>" — extract the object name for SDK
        prefix = f"s3://{MINIO_RECONSTRUCTION_BUCKET}/"
        glb_object_name = glb_location.removeprefix(prefix)

        # 2. Download GLB from MinIO and render thumbnail
        png_bytes = render_glb_thumbnail(glb_object_name)
        if not png_bytes:
            return {'error': 'Thumbnail rendering failed'}, 500

        # 3. Upload PNG to Directus
        filename = f"{object_id}_thumbnail.png"
        file_id = upload_file(png_bytes, filename)
        if not file_id:
            return {'error': 'Failed to upload thumbnail to Directus'}, 502

        # 4. Optionally update the artefact's thumbnail field
        if artefact_id:
            if not set_artefact_thumbnail(artefact_id, file_id):
                logger.warning(f"Thumbnail uploaded (id={file_id}) but artefact update failed for {artefact_id}")
                return {
                    'file_id': file_id,
                    'warning': f"Thumbnail created but could not update artefact '{artefact_id}'"
                }, 207

        return {'file_id': file_id}, 201
