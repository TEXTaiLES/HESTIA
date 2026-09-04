from flask import request
from datetime import datetime, timezone
import uuid
import json
import logging

from services.utils import upload_filestorage
from resources.resource_base import ResourceBase
from services.database import get_db_connection
from services.storage import (
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


class RobotImageResource(ResourceBase):
    avro_schema = """
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
    table = "robot_images"
    order_by = "timestamp ASC"

    def build_GET_conditions(self):
        conditions = []
        if scan_id := request.args.get('scan_id'):
            conditions.append(('scan_id', '=', scan_id))
        return conditions

    def post(self):
        files = request.files.getlist('file')

        # Validate
        if not files or files[0].filename == '':
            return {'error': "No file(s) provided."}, 400
        if 'metadata_map' not in request.form:
            return {'error': "No 'metadata_map' provided."}, 400

        try:
            metadata_map = json.loads(request.form.get('metadata_map'))
        except json.JSONDecodeError:
            return {'error': "Invalid JSON in 'metadata_map' field."}, 400

        scan_id = request.form.get('scan_id') or str(uuid.uuid4())

        uploaded_images = []
        for file in files:
            filename = file.filename
            metadata = metadata_map.get(filename, {})
            try:
                object_name = f"{scan_id}/{file.filename}"
                upload_filestorage(MINIO_ROBOT_BUCKET, object_name, file)
                if result := self.store_record_in_db(
                    file, metadata, scan_id, request.form.get('artifact_id')
                ):
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

    def store_record_in_db(self, file, metadata: dict, scan_id: str, artifact_id: str = None) -> dict:
        """Helper function to process a single file upload."""

        # Construct metadata record to be inserted in DB
        image_id = str(uuid.uuid4())
        object_name = f"{scan_id}/{file.filename}"
        record = {
            'image_id': image_id,
            'scan_id': scan_id,
            'filename': file.filename,
            'location': f"s3://{MINIO_ROBOT_BUCKET}/{object_name}",
            'public_url': build_public_url(MINIO_ROBOT_BUCKET, object_name),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'robot_pose': metadata.get('robot_pose'),
            'artifact_id': metadata.get('artifact_id') or artifact_id,
        }

        # Validate with Avro
        if not (is_validated := send_avro_message(
            TOPIC_ROBOT_IMAGES,
            image_id,
            record,
            self.avro_schema
        )):
            raise Exception(f"Failed to send robot image {image_id} to Kafka.")

        # Execute INSERT query
        keys = [key for key in record.keys()]
        sql = f"INSERT INTO robot_images ({', '.join(keys)})"
        sql += f" VALUES ({', '.join(['%s' for _ in keys])})"
        params = [record[key] for key in keys]
        conn = None
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                if cur.rowcount == 0:
                    raise Exception(f"Robot image {image_id} could not be stored in DB.")
        finally:
            if conn is not None:
                conn.close()

        # Notify Listeners via Kafka
        notification = {
            'image_id': image_id,
            'scan_id': scan_id,
            'event_type': TOPIC_ROBOT_UPLOADED,
            'event_timestamp': datetime.now(timezone.utc).isoformat()
        }
        send_simple_message(TOPIC_ROBOT_UPLOADED, image_id, notification)

        return record
