from flask import request, jsonify
from flask_restful import Resource
from werkzeug.utils import secure_filename
from datetime import datetime, timezone
import uuid
import io
import os
import json
import logging
import shutil

from middleware.security import require_api_key
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

try:
    from services.utils import convert_obj_to_glb
except ImportError:
    convert_obj_to_glb = None

logger = logging.getLogger(__name__)

RECONSTRUCTION_AVRO_SCHEMA = """
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
        {"name": "timestamp", "type": "string"}
    ]
}
"""

class ReconstructionResource(Resource):
    method_decorators = [require_api_key]

    def get(self):
        """
        Retrieve reconstructions from the Read-Database (populated by Kafka).
        """
        conn = None
        try:
            scan_id = request.args.get('scan_id')
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 50))
            offset = (page - 1) * per_page

            sql = "SELECT * FROM reconstructions WHERE 1=1"
            params = []

            if scan_id:
                sql += " AND scan_id = %s"
                params.append(scan_id)

            sql += " ORDER BY timestamp DESC LIMIT %s OFFSET %s"
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
            logger.error(f"Error fetching reconstructions: {e}")
            if conn: conn.close()
            return {'error': "Reconstructions not available yet (Kafka Sync pending) or DB Error."}, 500

    def post(self):
        files = request.files.getlist('file')
        if not files or files[0].filename == '':
            return {'error': "No file(s) provided."}, 400

        scan_id = request.form.get('scan_id')
        if not scan_id:
             return {'error': "No 'scan_id' provided."}, 400

        object_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # Initialize Record
        record = {
            'object_id': object_id,
            'scan_id': scan_id,
            'filename': files[0].filename,
            'timestamp': timestamp,
            'model_location': None,
            'texture_location': None,
            'material_location': None,
            'glb_location': None,
            'public_url_model': None,
            'public_url_texture': None,
            'public_url_material': None,
            'public_url_glb': None
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

                if filename.lower().endswith('.obj'): obj_local_path = local_path

                # Upload to Minio
                object_name = f"{object_id}/raw/{filename}"
                minio_client.put_object(
                    MINIO_RECONSTRUCTION_BUCKET,
                    object_name,
                    file,
                    os.fstat(file.fileno()).st_size,
                    content_type=file.content_type
                )

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

                if convert_obj_to_glb(obj_local_path, glb_local_path):
                    glb_object_name = f"{object_id}/model.glb"
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

            # 3. Publish to Kafka
            if send_avro_message(TOPIC_RECONSTRUCTIONS, object_id, record, RECONSTRUCTION_AVRO_SCHEMA):

                # 4. Notify Listeners
                send_simple_message(TOPIC_RECONSTRUCTION_UPLOADED, object_id, {'status': 'completed'})

                return {
                    'message': "Reconstruction queued for processing.",
                    'object_id': object_id,
                    'data': record
                }, 201
            else:
                 return {'error': "Failed to publish to Kafka"}, 500

        except Exception as e:
            logger.error(f"Reconstruction processing failed: {str(e)}")
            return {'error': str(e)}, 500
        finally:
            if os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir)