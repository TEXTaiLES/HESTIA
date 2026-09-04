from flask import request
from werkzeug.utils import secure_filename
from datetime import datetime, timezone
import uuid
import io
import os
import logging
import shutil

from services.utils import convert_obj_to_glb, upload_filestorage
from resources.resource_base import ResourceBase
from services.database import get_db_connection
from services.storage import (
    minio_client,
    build_public_url,
    MINIO_RECONSTRUCTION_BUCKET
)
from services.messaging import (
    send_avro_message,
    send_simple_message,
    TOPIC_RECONSTRUCTIONS,
    TOPIC_RECONSTRUCTION_UPLOADED
)

logger = logging.getLogger(__name__)


class ReconstructionResource(ResourceBase):
    avro_schema = """
    {
        "type": "record",
        "name": "Reconstruction",
        "namespace": "com.textailes.reconstruction",
        "fields": [
            {"name": "object_id", "type": "string"},
            {"name": "scan_id", "type": "string"},
            {"name": "filename", "type": "string"},
            {"name": "model_location", "type": ["null", "string"], "default": null},
            {"name": "texture_location", "type": ["null", "string"], "default": null},
            {"name": "material_location", "type": ["null", "string"], "default": null},
            {"name": "glb_location", "type": ["null", "string"], "default": null},
            {"name": "public_url_model", "type": ["null", "string"], "default": null},
            {"name": "public_url_texture", "type": ["null", "string"], "default": null},
            {"name": "public_url_material", "type": ["null", "string"], "default": null},
            {"name": "public_url_glb", "type": ["null", "string"], "default": null},
            {"name": "timestamp", "type": "string"},
            {"name": "artifact_id", "type": ["null", "string"], "default": null}
        ]
    }
    """
    table = "reconstructions"
    order_by = "timestamp DESC"

    def build_GET_conditions(self):
        conditions = []
        if scan_id := request.args.get('scan_id'):
            conditions.append(('scan_id', '=', scan_id))
        return conditions

    def post(self):
        files = request.files.getlist('file')
        if not files or files[0].filename == '':
            return {'error': "No file(s) provided."}, 400

        scan_id = request.form.get('scan_id')
        if not scan_id:
             return {'error': "No 'scan_id' provided."}, 400

        artifact_id = request.form.get('artifact_id')

        object_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # Initialize Record
        record = {
            'object_id': object_id,
            'scan_id': scan_id,
            'filename': None,
            'timestamp': timestamp,
            'model_location': None,
            'texture_location': None,
            'material_location': None,
            'glb_location': None,
            'public_url_model': None,
            'public_url_texture': None,
            'public_url_material': None,
            'public_url_glb': None,
            'artifact_id': artifact_id,
        }

        tmp_dir = f"/tmp/rec_{object_id}"
        os.makedirs(tmp_dir, exist_ok=True)
        obj_local_path = None

        try:
            # 1. Process & Upload Raw Files
            for file in files:
                filename = secure_filename(file.filename)

                # Save locally for conversion
                local_path = os.path.join(tmp_dir, filename)
                file.save(local_path)
                file.seek(0)

                if filename.lower().endswith('.obj'):
                    obj_local_path = local_path
                    if record['filename'] is None:
                        record['filename'] = filename

                # Upload to Minio. upload_filestorage sizes the spooled stream by
                # seeking it, which also works for the small uploads werkzeug keeps
                # in memory (those have no fileno() to fstat).
                object_name = f"{object_id}/raw/{filename}"
                upload_filestorage(MINIO_RECONSTRUCTION_BUCKET, object_name, file)

                # Update Record
                s3_loc = f"s3://{MINIO_RECONSTRUCTION_BUCKET}/{object_name}"
                pub_url = build_public_url(MINIO_RECONSTRUCTION_BUCKET, object_name)

                ext = filename.split('.')[-1].lower()
                if ext == 'obj':
                    record['model_location'] = s3_loc
                    record['public_url_model'] = pub_url
                elif ext == 'png' or ext == 'jpg':
                    record['texture_location'] = s3_loc
                    record['public_url_texture'] = pub_url
                elif ext == 'mtl':
                    record['material_location'] = s3_loc
                    record['public_url_material'] = pub_url

            # 2. Convert to GLB (Integration Step)
            if convert_obj_to_glb and obj_local_path:
                glb_filename = f"{object_id}.glb"
                glb_local_path = os.path.join(tmp_dir, glb_filename)
                filename = "model.glb"

                if convert_obj_to_glb(obj_local_path, glb_local_path):
                    glb_object_name = f"{object_id}/{filename}"
                    with open(glb_local_path, 'rb') as glb_file:
                        file_data = glb_file.read()
                        minio_client.put_object(
                            MINIO_RECONSTRUCTION_BUCKET,
                            glb_object_name,
                            io.BytesIO(file_data),
                            len(file_data),
                            content_type="model/gltf-binary"
                        )
                    record['glb_location'] = f"s3://{MINIO_RECONSTRUCTION_BUCKET}/{glb_object_name}"
                    record['public_url_glb'] = build_public_url(MINIO_RECONSTRUCTION_BUCKET, glb_object_name)
                    record['filename'] = filename

            # Ensure filename is never None (Avro schema requires a string) eg. if only a texture was uploaded without an OBJ
            if record['filename'] is None:
                record['filename'] = secure_filename(files[0].filename)

            # 3. Publish to Kafka
            if not send_avro_message(TOPIC_RECONSTRUCTIONS, object_id, record, self.avro_schema):
                 return {'error': "Failed to publish to Kafka"}, 500

            keys = [key for key in record.keys()]
            sql = f"INSERT INTO reconstructions ({', '.join(record.keys())})"
            sql += f" VALUES ({', '.join(['%s' for _ in record.keys()])})"
            params = [record[key] for key in record.keys()]

            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                if cur.rowcount == 0:
                    raise Exception(f"Object {object_id} could not be stored in DB.")

            # 4. Notify Listeners
            send_simple_message(TOPIC_RECONSTRUCTION_UPLOADED, object_id, {'status': 'completed'})

            return {
                'message': "Reconstruction queued for processing.",
                'object_id': object_id,
                'data': record
            }, 201

        except Exception as e:
            logger.error(f"Reconstruction processing failed: {str(e)}")
            return {'error': str(e)}, 500
        finally:
            if os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir)
