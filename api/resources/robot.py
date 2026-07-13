from flask import request, jsonify
from flask_restful import Resource
from datetime import datetime, timezone
import uuid
import io
import json
import logging

from middleware.security import require_api_key
from services.database import get_db_connection
from services.storage import (
    minio_client,
    build_public_url,
    MINIO_ROBOT_BUCKET
)
from services.messaging import (
    send_avro_message,
    send_simple_message,
    TOPIC_ROBOT_IMAGES,
    TOPIC_ROBOT_UPLOADED
)

logger = logging.getLogger(__name__)

# Avro Schema Definition
ROBOT_AVRO_SCHEMA = """
{
    "type": "record",
    "name": "RobotImage",
    "namespace": "com.textailes.robot",
    "fields": [
        {"name": "image_id", "type": "string"},
        {"name": "scan_id", "type": "string"},
        {"name": "filename", "type": "string"},
        {"name": "location", "type": "string"},
        {"name": "public_url", "type": ["null", "string"], "default": null},
        {"name": "timestamp", "type": "string"},
        {"name": "robot_pose", "type": ["null", "string"], "default": null},
        {"name": "artifact_id", "type": ["null", "string"], "default": null}
    ]
}
"""


class RobotImageResource(Resource):
    method_decorators = [require_api_key]

    def get(self):
        """
        Retrieve robot images.

        Query Params:
            scan_id (str): The Batch/Scan ID.
            page (int): Pagination page.
            per_page (int): Items per page.
        """
        conn = None
        try:
            scan_id = request.args.get('scan_id')
            try:
                page = int(request.args.get('page', 1))
                per_page = int(request.args.get('per_page', 50))
            except ValueError:
                return {'error': 'Page and per_page must be integers'}, 400
            offset = (page - 1) * per_page

            # Base Query
            sql = "SELECT * FROM robot_images WHERE 1=1"
            params = []

            if scan_id:
                sql += " AND scan_id = %s"
                params.append(scan_id)

            sql += " ORDER BY timestamp ASC LIMIT %s OFFSET %s"
            params.extend([per_page, offset])

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(sql, tuple(params))

            rows = cur.fetchall()
            results = []
            if cur.description:
                colnames = [desc[0] for desc in cur.description]
                for row in rows:
                    row_dict = {}
                    for col, val in zip(colnames, row):
                        if isinstance(val, datetime):
                            row_dict[col] = val.isoformat()
                        else:
                            row_dict[col] = val
                    results.append(row_dict)

            cur.close()
            conn.close()
            return jsonify(results)

        except Exception as e:
            logger.error(f"Error fetching robot images: {e}")
            if conn: conn.close()
            return {'error': str(e)}, 500

    def post(self):
        files = request.files.getlist('file')

        if not files or files[0].filename == '':
            return {'error': "No file(s) provided."}, 400
        if 'metadata_map' not in request.form:
            return {'error': "No 'metadata_map' provided."}, 400

        try:
            metadata_map = json.loads(request.form.get('metadata_map'))
        except json.JSONDecodeError:
            return {'error': "Invalid JSON in 'metadata_map' field."}, 400

        # Upload

        # Generate a unique Scan ID if not already provided
        scan_id = request.form.get('scan_id')
        if not scan_id:
            scan_id = str(uuid.uuid4())

        artifact_id = request.form.get('artifact_id')

        uploaded_images = []

        for file in files:
            filename = file.filename

            metadata = metadata_map.get(filename, {})

            try:
                result = self.upload_single_file(file, metadata, scan_id, artifact_id)
                if result:
                    uploaded_images.append(result)
            except Exception as e:
                logger.error(f"Failed to upload file {filename}: {str(e)}")

        if not uploaded_images:
            return {'error': 'File upload failed.'}, 500

        return {
            'message': f"Successfully processed {len(uploaded_images)} file(s).",
            'scan_id': scan_id,
            'uploaded_files': uploaded_images
        }, 201

    def upload_single_file(self, file, metadata: dict, scan_id: str, artifact_id: str = None) -> dict:
        """Helper function to process a single file upload."""
        image_id = str(uuid.uuid4())

        # STEP 1: Upload to MinIO
        file_data = file.read()
        object_name = f"{scan_id}/{file.filename}" # Group by scan_id in folders
        minio_client.put_object(
            MINIO_ROBOT_BUCKET,
            object_name,
            io.BytesIO(file_data),
            len(file_data),
            content_type=file.content_type or 'application/octet-stream'
        )

        # STEP 2: Prepare Metadata

        location = f"s3://{MINIO_ROBOT_BUCKET}/{object_name}"
        public_url = build_public_url(MINIO_ROBOT_BUCKET, object_name)
        robot_pose = metadata.get('robot_pose')
        timestamp = datetime.now(timezone.utc).isoformat()

        record = {
            'image_id': image_id,
            'scan_id': scan_id,
            'filename': file.filename,
            'location': location,
            'public_url': public_url,
            'timestamp': timestamp,
            'robot_pose': robot_pose,
            'artifact_id': metadata.get('artifact_id') or artifact_id,
        }

        # STEP 3: Send to Kafka (Storage)
        if not send_avro_message(TOPIC_ROBOT_IMAGES, image_id, record, ROBOT_AVRO_SCHEMA):
            raise Exception(f"Failed to send robot image {image_id} to Kafka.")

        keys = [key for key in record.keys()]
        sql = f"INSERT INTO robot_images ({', '.join(keys)})"
        sql += f" VALUES ({', '.join(['%s' for _ in keys])})"
        params = [record[key] for key in keys]

        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            if cur.rowcount == 0:
                raise Exception(f"Robot image {image_id} could not be stored in DB.")

        # STEP 4: Notify Listeners
        notification = {
            'image_id': image_id,
            'scan_id': scan_id,
            'event_type': TOPIC_ROBOT_UPLOADED,
            'event_timestamp': timestamp
        }
        send_simple_message(TOPIC_ROBOT_UPLOADED, image_id, notification)

        return record
