from flask import request, jsonify
from flask_restful import Resource
from datetime import datetime, timezone
import uuid
import io
import os
import json
import logging

from middleware.security import require_api_key
from services.database import get_db_connection
from services.storage import (
    minio_client,
    build_public_url,
    MINIO_ANNOTATION_BUCKET
)
from services.messaging import (
    send_avro_message,
    send_simple_message,
    TOPIC_ANNOTATIONS,
    TOPIC_ANNOTATION_UPLOADED
)

logger = logging.getLogger(__name__)

# Avro Schema for Annotations (Scenes)
ANNOTATION_AVRO_SCHEMA = """
{
    "type": "record",
    "name": "Annotation",
    "namespace": "com.textailes.annotation",
    "fields": [
        {"name": "scene_id", "type": "string"},
        {"name": "public_url", "type": ["null", "string"], "default": null},
        {"name": "location", "type": ["null", "string"], "default": null},
        {"name": "content", "type": ["null", "string"], "default": null},
        {"name": "timestamp", "type": "string"}
    ]
}
"""

class AnnotationResource(Resource):
    method_decorators = [require_api_key]

    def get(self):
        """Retrieve annotations/scenes from DB (populated by Kafka Sink)."""
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute("SELECT * FROM annotations ORDER BY timestamp DESC LIMIT 50")
            rows = cur.fetchall()

            results = []
            if cur.description:
                colnames = [desc[0] for desc in cur.description]
                for row in rows:
                    row_dict = dict(zip(colnames, row))

                    for k, v in row_dict.items():
                        if isinstance(v, datetime):
                            row_dict[k] = v.isoformat()
                    results.append(row_dict)

            cur.close()
            conn.close()
            return jsonify(results)
        except Exception as e:
            if conn: conn.close()
            return {'error': str(e)}, 500

    def post(self):
        files = request.files.getlist('file')
        scene_json_str = request.form.get('scene')

        if not files or not scene_json_str:
            return {'error': "Files or Scene JSON missing."}, 400

        try:
            scene_data = json.loads(scene_json_str)
        except:
            return {'error': "Invalid JSON"}, 400

        scene_id = scene_data.get('scene_id', str(uuid.uuid4()))
        timestamp = datetime.now(timezone.utc).isoformat()

        main_file_url = None
        main_file_location = None

        try:
            # 1. Upload Files
            for file in files:
                object_name = f"{scene_id}/{file.filename}"
                minio_client.put_object(
                    MINIO_ANNOTATION_BUCKET,
                    object_name,
                    file,
                    os.fstat(file.fileno()).st_size,
                    content_type=file.content_type
                )
                if not main_file_url:
                    main_file_url = build_public_url(MINIO_ANNOTATION_BUCKET, object_name)
                    main_file_location = f"s3://{MINIO_ANNOTATION_BUCKET}/{object_name}"

            # 2. Prepare Record
            record = {
                'scene_id': scene_id,
                'public_url': main_file_url,
                'location': main_file_location,
                'content': json.dumps(scene_data),
                'timestamp': timestamp
            }

            # 3. Send to Kafka
            if send_avro_message(TOPIC_ANNOTATIONS, scene_id, record, ANNOTATION_AVRO_SCHEMA):
                send_simple_message(TOPIC_ANNOTATION_UPLOADED, scene_id, {'status': 'saved'})
                return {'message': "Scene saved", 'scene_id': scene_id}, 201
            else:
                return {'error': "Failed to send to Kafka"}, 500

        except Exception as e:
            logger.error(f"Annotation save failed: {e}")
            return {'error': str(e)}, 500
