from flask import request, jsonify
from flask_restful import Resource
from datetime import datetime, timezone
import uuid
import os
import json
import logging

from middleware.security import require_api_key
from services.database import get_db_connection
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
        {"name": "object_id", "type": "string"},
        {"name": "public_url", "type": ["null", "string"], "default": null},
        {"name": "location", "type": ["null", "string"], "default": null},
        {"name": "collaborative", "type": "boolean", "default": false},
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

            object_id = request.args.get('object_id')
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 50))
            offset = (page - 1) * per_page

            sql = "SELECT * FROM annotations WHERE 1=1"
            params: list = []

            if object_id:
                sql += " AND object_id = %s"
                params.append(object_id)

            sql += " ORDER BY timestamp DESC LIMIT %s OFFSET %s"
            params.extend([per_page, offset])

            cur.execute(sql, tuple(params))
            rows: list[tuple] = cur.fetchall()

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
                return {'scenes': results}, 200 if results else 204

            if object_id:
                sql = "SELECT * FROM reconstructions WHERE object_id = %s"
                cur.execute(sql, (object_id,))
                if cur.description is None:
                    return {'error': f"Object '{object_id}' does not exist."}, 400

                row = cur.fetchone()

                # Prepare Record
                scene_id = str(uuid.uuid4())
                timestamp = datetime.now(timezone.utc).isoformat()

                colnames = [desc[0] for desc in cur.description]
                filename = row[colnames.index('filename')]
                public_url = row[colnames.index('public_url_glb')]
                location = row[colnames.index('glb_location')]

                scene_data: dict = {
                    "scene_id": scene_id,
                    "object_id": object_id,
                    "status": "complete",
                    "collaborative": False,
                    "scenegraph": {
                        "nodes": {
                            filename: {"urls": [public_url]}
                        },
                        "edges": {".": [filename]}
                    }
                }

                record = {
                    'scene_id': scene_id,
                    'object_id': object_id,
                    'public_url': public_url,
                    'location': location,
                    'collaborative': scene_data['collaborative'],
                    'content': json.dumps(scene_data),
                    'timestamp': timestamp
                }

                # Verify with Avro
                if not send_avro_message(TOPIC_ANNOTATIONS, scene_id, record, ANNOTATION_AVRO_SCHEMA):
                    raise Exception("Failed to verify with Avro")

                # Insert/Update record in DB
                attributes = [key for key in record if record[key]]
                sql = f"INSERT INTO annotations ({', '.join(attributes)})"
                sql += f" VALUES ({', '.join(['%s' for _ in attributes])})"
                params = [record[attribute] for attribute in attributes]

                cur.execute(sql, tuple(params))
                if cur.rowcount == 0:
                    raise Exception(f"Scene with object '{object_id}' could not be stored in DB.")

                # Send to Kafka
                if not send_simple_message(TOPIC_ANNOTATION_UPLOADED, scene_id, {'status': 'completed'}):
                    raise Exception("Failed to send to Kafka")

                cur.close()
                conn.close()

                return {
                    'message': "Scene created successfully.",
                    'data': record
                }, 201

        except Exception as e:
            if conn: conn.close()
            return {'error': str(e)}, 500

    def post(self):
        scene_data = request.get_json()
        if not scene_data:
            return {'error': "No data provided"}, 400

        scene_id = scene_data.get('scene_id', str(uuid.uuid4()))
        object_id = scene_data.get('object_id')
        if object_id is None:
            return {'error': "Missing 'object_id'"}, 400

        timestamp = datetime.now(timezone.utc).isoformat()
        collaborative = scene_data.get('collaborative', False)

        filename = next(iter(scene_data['scenegraph']['nodes']))
        public_url = scene_data["scenegraph"]["nodes"][filename]["urls"][0]

        try:
            # Prepare Record
            record = {
                'scene_id': scene_id,
                'object_id': object_id,
                'collaborative': collaborative,
                'public_url': public_url,
                'location': None,
                'content': json.dumps(scene_data),
                'timestamp': timestamp
            }

            # Verify with Avro
            if not send_avro_message(TOPIC_ANNOTATIONS, scene_id, record, ANNOTATION_AVRO_SCHEMA):
                raise Exception("Failed to verify with Avro")

            # Insert/Update record in DB
            with get_db_connection() as conn, conn.cursor() as cur:
                attributes = [key for key in record if record[key]]

                sql = "WITH cleanup AS (DELETE FROM annotations WHERE scene_id = %s OR object_id = %s)\n"
                params = [scene_id, object_id]

                sql += f"INSERT INTO annotations ({', '.join(attributes)})\n"
                sql += f"VALUES ({', '.join(['%s' for _ in attributes])});"
                params.extend([record[attribute] for attribute in attributes])

                cur.execute(sql, tuple(params))
                if cur.rowcount == 0:
                    raise Exception(f"Scene '{scene_id}' could not be stored in DB.")

                sql = "UPDATE annotations SET location = reconstructions.glb_location FROM reconstructions\n"
                sql += "WHERE annotations.object_id = reconstructions.object_id AND annotations.object_id = %s;"
                params = [object_id]

                cur.execute(sql, tuple(params))
                if cur.rowcount == 0:
                    # QUESTION: Is this really a 'warning' or 'info' message?
                    logger.warning(f"Could not update the `location` value of the upserted scene record '{scene_id}'.")

                # QUESTION: We could replace the 'UPDATE' query by reconstructing the 'location'
                #           using the reconstructions bucket and the object_id.
                #           This way this case will never happen. What's the best alternative?

            # Send to Kafka
            if not send_simple_message(TOPIC_ANNOTATION_UPLOADED, scene_id, {'status': 'saved'}):
                raise Exception("Failed to send to Kafka")

            return {'message': "Scene saved", 'scene_id': scene_id}, 201

        except Exception as e:
            logger.error(f"Failed to save scene: {e}")
            return {'error': str(e)}, 500
