from flask import request, jsonify, current_app
from flask_restful import Resource
import uuid
import io
import json
import logging
from datetime import datetime, timezone

from middleware.security import require_api_key
from services.database import get_db_connection
from services.storage import (
    minio_client,
    build_public_url,
    MINIO_ARTIFACT_BUCKET
)
from services.messaging import (
    send_avro_message,
    send_simple_message,
    TOPIC_ARTIFACTS,
    TOPIC_ARTIFACT_UPLOADED
)

logger = logging.getLogger(__name__)

ARTIFACT_AVRO_SCHEMA = """
{
    "type": "record",
    "name": "ArtifactMetadata",
    "namespace": "com.textailes.artifact",
    "fields": [
        {"name": "artifact_id", "type": "string"},
        {"name": "title", "type": ["null", "string"], "default": null},
        {"name": "filename", "type": "string"},
        {"name": "location", "type": "string"},
        {"name": "public_url", "type": ["null", "string"], "default": null},
        {"name": "uploaded_by", "type": ["null", "string"], "default": null},
        {"name": "timestamp", "type": ["null", "long"], "logicalType": "timestamp-millis", "default": null},
        {"name": "drone_id", "type": ["null", "string"], "default": null}
    ]
}
"""

class ArtifactResource(Resource):
    method_decorators = [require_api_key]

    def post(self):
        """Handle file uploads (single or batch with metadata map)."""
        files = request.files.getlist('file')
        if not files or files[0].filename == '':
            return {'error': 'No file(s) provided'}, 400

        uploaded_artifacts = []

        # Batch Upload Logic
        if 'metadata_map' in request.form:
            try:
                metadata_map = json.loads(request.form.get('metadata_map'))
            except json.JSONDecodeError:
                return {'error': "Invalid JSON in 'metadata_map' field"}, 400

            for file in files:
                filename = file.filename
                metadata = metadata_map.get(filename, {})

                try:
                    result = self.upload_single_file(
                        file,
                        filename,
                        metadata.get('title', filename),
                        metadata.get('uploaded_by', 'user123'),
                        metadata.get('drone_id', 'unknown_drone')
                    )
                    if result: uploaded_artifacts.append(result)
                except Exception as e:
                    logger.error(f"Failed to upload file {filename}: {str(e)}")

        # Single File Logic
        else:
            if len(files) > 1:
                return {'error': 'Ambiguous request. For multiple files, use "metadata_map".'}, 400

            file = files[0]
            try:
                result = self.upload_single_file(
                    file,
                    file.filename,
                    request.form.get('title', file.filename),
                    request.form.get('uploaded_by', 'user123'),
                    request.form.get('drone_id', 'unknown_drone')
                )
                if result: uploaded_artifacts.append(result)
            except Exception as e:
                logger.error(f"Failed to upload file {file.filename}: {str(e)}")

        if not uploaded_artifacts:
            return {'error': 'File upload failed'}, 500

        return {
            "message": f"Successfully processed {len(uploaded_artifacts)} file(s)",
            "uploaded_files": uploaded_artifacts
        }, 201

    def upload_single_file(self, file, filename, title, uploaded_by, drone_id):
        """Helper to process a single file upload."""
        artifact_id = str(uuid.uuid4())
        timestamp_millis = int(datetime.now(timezone.utc).timestamp() * 1000)

        # 1. Upload to MinIO
        file_data = file.read()
        object_name = f"{artifact_id}/{filename}"

        minio_client.put_object(
            MINIO_ARTIFACT_BUCKET, object_name, io.BytesIO(file_data), len(file_data),
            content_type=file.content_type or 'application/octet-stream'
        )

        # 2. Prepare Metadata
        location = f"s3://{MINIO_ARTIFACT_BUCKET}/{object_name}"
        public_url = build_public_url(MINIO_ARTIFACT_BUCKET, object_name)

        record = {
            "artifact_id": artifact_id, "title": title, "filename": filename,
            "location": location, "public_url": public_url, "uploaded_by": uploaded_by,
            "timestamp": timestamp_millis, "drone_id": drone_id
        }

        # 3. Send to Kafka (Storage)
        if not send_avro_message(TOPIC_ARTIFACTS, artifact_id, record, ARTIFACT_AVRO_SCHEMA):
            raise Exception(f"Failed to send artifact {artifact_id} to Kafka")

        # 4. Notify Listeners
        notification = {
            # QUESTION: Should 'artifact_uploaded' get replaced with TOPIC_ARTIFACT_UPLOADED ?
            "artifact_id": artifact_id, "event_type": TOPIC_ARTIFACT_UPLOADED,
            "event_timestamp": datetime.now(timezone.utc).isoformat()
        }
        send_simple_message(TOPIC_ARTIFACT_UPLOADED, artifact_id, notification)

        return {
            "artifact_id": artifact_id, "drone_id": drone_id,
            "location": location, "filename": filename,
            "public_url": public_url, "title": title
        }

    def get(self):
        """Handle GET requests to fetch all artifacts."""
        conn = None
        try:
            filters = request.args.copy()
            sql_select = "SELECT * FROM artifacts"

            if 'fields' in filters:
                field_list_str = filters.pop('fields')
                safe_fields = [f for f in field_list_str.split(',') if f.replace('_', '').isalnum()]
                if safe_fields:
                    sql_select = f"SELECT {', '.join(safe_fields)} FROM artifacts"

            sql_query = sql_select
            query_params = []

            if filters:
                sql_query += " WHERE "
                filter_clauses = []
                for key, value in filters.items():
                    if key in ['drone_id', 'uploaded_by', 'title']:
                        filter_clauses.append(f"{key} = %s")
                        query_params.append(value)
                sql_query += " AND ".join(filter_clauses)

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(sql_query, query_params)

            artifacts = cur.fetchall()
            if cur.description:
                colnames = [desc[0] for desc in cur.description]
                result = [dict(zip(colnames, row)) for row in artifacts]
            else:
                result = []

            cur.close()
            conn.close()
            return jsonify(result)

        except Exception as e:
            if conn: conn.close()
            return {'error': str(e)}, 500

class ArtifactItemResource(Resource):
    method_decorators = [require_api_key]

    def get(self, artifact_id):
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM artifacts WHERE artifact_id = %s", (artifact_id,))
            artifact = cur.fetchone()

            if artifact:
                colnames = [desc[0] for desc in cur.description]
                result = dict(zip(colnames, artifact))
                cur.close()
                conn.close()
                return jsonify(result)
            else:
                cur.close()
                conn.close()
                return {'error': 'Artifact not found'}, 404

        except Exception as e:
            if conn: conn.close()
            return {'error': str(e)}, 500