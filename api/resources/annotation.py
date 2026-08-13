from flask import request, jsonify
from datetime import datetime, timezone
import uuid
import json
import logging

from resources.resource_base import ResourceBase, BadRequest
from services.database import get_db_connection
from services.messaging import (
    send_avro_message,
    send_simple_message,
    TOPIC_ANNOTATIONS,
    TOPIC_ANNOTATION_UPLOADED,
    TOPIC_ANNOTATION_MODIFIED
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
        {"name": "linked_objects", "type": ["null", "string"], "default": null},
        {"name": "timestamp", "type": "string"},
        {"name": "artifact_id", "type": ["null", "string"], "default": null}
    ]
}
"""


class AnnotationResource(ResourceBase):
    avro_schema = """
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
            {"name": "linked_objects", "type": ["null", "string"], "default": null},
            {"name": "timestamp", "type": "string"},
            {"name": "artifact_id", "type": ["null", "string"], "default": null}
        ]
    }
    """
    table = "annotations"
    order_by = "timestamp DESC"

    def build_GET_conditions(self):
        conditions = []
        if object_id := request.args.get('object_id'):
            conditions.append(('object_id', '=', object_id))
        return conditions

    def get(self):
        """Retrieve annotations/scenes from DB (populated by Kafka Sink)."""
        conn = None
        try:
            payload, status_code = super().get()
            if status_code != 200:
                return payload, status_code
            if payload:
                return {'scenes': payload}, 200
            return payload, 204

            if object_id := request.args.get('object_id'):
                sql = "SELECT * FROM reconstructions WHERE object_id = %s"
                with get_db_connection() as conn, conn.cursor() as cur:
                    cur.execute(sql, (object_id,))
                    if cur.description is None:
                        raise BadRequest(f"Object '{object_id}' does not exist.")

                row = cur.fetchone()

                # Prepare Record
                scene_id = str(uuid.uuid4())
                timestamp = datetime.now(timezone.utc).isoformat()

                colnames = [desc[0] for desc in cur.description]
                filename = row[colnames.index('filename')]
                public_url = row[colnames.index('public_url_glb')]
                location = row[colnames.index('glb_location')]
                rec_artifact_id = row[colnames.index('artifact_id')] if 'artifact_id' in colnames else None

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
                    },
                    "linked_objects": {}
                }

                record = {
                    'scene_id': scene_id,
                    'object_id': object_id,
                    'public_url': public_url,
                    'location': location,
                    'collaborative': scene_data['collaborative'],
                    'content': json.dumps(scene_data['scenegraph']),
                    'linked_objects': json.dumps(scene_data['linked_objects']),
                    'timestamp': timestamp,
                    'artifact_id': rec_artifact_id,
                }

                # Verify with Avro
                if not send_avro_message(TOPIC_ANNOTATIONS, scene_id, record, self.avro_schema):
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

        except BadRequest as e:
            return {'error': str(e)}, 400
        except Exception as e:
            return {'error': str(e)}, 500
        finally:
            if conn:
                conn.close()

    def post(self):
        scene_data = request.get_json()
        if not scene_data:
            return {'error': "No data provided"}, 400

        # NOTE: Remove scene_id from JSON input?
        scene_id = scene_data.get('scene_id', str(uuid.uuid4()))
        object_id = scene_data.get('object_id')
        if object_id is None:
            return {'error': "Missing 'object_id'"}, 400

        try:
            scenegraph = scene_data.get('scenegraph', {})
            if not scenegraph:
                raise KeyError("scenegraph is empty or missing")

            nodes = scenegraph.get('nodes', {})
            if not nodes:
                raise KeyError("scenegraph.nodes is empty or missing")

            filename = next(iter(nodes))
            public_url = nodes[filename]['urls'][0]
        except (KeyError, IndexError, TypeError) as e:
            return {'error': f"Invalid scene structure: {str(e)}"}, 400

        timestamp = datetime.now(timezone.utc).isoformat()
        collaborative = scene_data.get('collaborative', False)
        linked_objects: dict = scene_data.get('linked_objects', None)

        # If caller provides artifact_id, it overrides the reconstruction's value.
        override_artifact_id = scene_data.get('artifact_id')

        try:
            # Prepare Record
            record = {
                'scene_id': scene_id,
                'object_id': object_id,
                'collaborative': collaborative,
                'public_url': public_url,
                'location': None,
                'content': json.dumps(scene_data['scenegraph']),
                'linked_objects': json.dumps(linked_objects) if linked_objects else None,
                'timestamp': timestamp,
                'artifact_id': override_artifact_id,
            }

            # Verify with Avro
            if not send_avro_message(TOPIC_ANNOTATIONS, scene_id, record, ANNOTATION_AVRO_SCHEMA):
                raise Exception("Failed to verify with Avro")

            # Insert record in DB. artifact_id is sourced from the joined
            # reconstruction unless the caller supplied an explicit override.
            with get_db_connection() as conn, conn.cursor() as cur:
                sql = """
                    INSERT INTO annotations (scene_id, object_id, collaborative, public_url, content, linked_objects, timestamp, location, artifact_id)
                    SELECT %s, %s, %s, %s, %s, %s, %s, r.glb_location, COALESCE(%s, r.artifact_id)
                    FROM reconstructions r
                    WHERE r.object_id = %s
                    RETURNING scene_id, artifact_id;
                """
                params = (
                    record['scene_id'],
                    record['object_id'],
                    record['collaborative'],
                    record['public_url'],
                    record['content'],
                    record['linked_objects'],
                    record['timestamp'],
                    override_artifact_id,
                    record['object_id'],
                )

                cur.execute(sql, params)
                inserted = cur.fetchone()
                if not inserted:
                    return {'error': f"Scene could not be stored; no reconstruction found for object_id '{object_id}'"}, 404
                record['artifact_id'] = inserted[1]

            # Send to Kafka
            if not send_simple_message(TOPIC_ANNOTATION_UPLOADED, scene_id, {'status': 'saved'}):
                raise Exception("Failed to send to Kafka")

            return {'message': "Scene saved", 'scene_id': scene_id}, 201

        except Exception as e:
            logger.error(f"Failed to save scene: {e}")
            return {'error': str(e)}, 500

    def patch(self):
        update_data = request.get_json()
        if not update_data:
            return {'error': "No data provided"}, 400

        scene_id = update_data.get('scene_id')
        object_id = update_data.get('object_id')
        if scene_id is None and object_id is None:
            return {'error': "Missing 'scene_id' and 'object_id'"}, 400

        allowed_fields = {'collaborative', 'scenegraph', 'linked_objects'}
        fields_to_update: dict = {k: v for k, v in update_data.items() if k in allowed_fields}

        if not fields_to_update:
            return {'error': "No valid fields provided for update"}, 400

        fields_to_update['timestamp'] = datetime.now(timezone.utc).isoformat()

        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                id_key = 'scene_id' if scene_id else 'object_id'
                id_value = scene_id if scene_id else object_id

                sql = f"SELECT content, linked_objects FROM annotations WHERE {id_key} = %s;"
                params = [id_value]
                cur.execute(sql, tuple(params))

                row = cur.fetchone()
                if not row:
                    return {'error': f"No scene was found with {id_key} '{id_value}'"}, 400

                def get_changes(structure_dict: dict, dict_to_store_the_change: dict):
                    for primary_field in [primary_field for primary_field in structure_dict.keys() if primary_field in fields_to_update]:
                        for secondary_field in [secondary_field for secondary_field in structure_dict[primary_field].keys() if secondary_field in fields_to_update[primary_field]]:
                            dict_to_store_the_change[secondary_field] = fields_to_update[primary_field][secondary_field]

                content = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                content = content or {}
                get_changes({'scenegraph': {'nodes': [], 'edges': []}}, content)

                linked_objects = json.loads(row[1]) if isinstance(row[1], str) else row[1]
                linked_objects = linked_objects or {}
                get_changes({'linked_objects': {'parent_object': None, 'child_objects': []}}, linked_objects)

                update_clause = ['timestamp = %s']
                params = [fields_to_update['timestamp']]

                def append_field_in_update_clause(field: str, col_name: str, value):
                    if field in fields_to_update:
                        update_clause.append(f"{col_name} = %s")
                        params.append(value)

                append_field_in_update_clause('collaborative', 'collaborative', fields_to_update.get('collaborative'))
                append_field_in_update_clause('scenegraph', 'content', json.dumps(content))
                append_field_in_update_clause('linked_objects', 'linked_objects', json.dumps(linked_objects))

                sql = f"UPDATE annotations SET {', '.join(update_clause)} WHERE {id_key} = %s RETURNING {id_key};"
                params.append(id_value)

                cur.execute(sql, tuple(params))
                if cur.rowcount == 0:
                    raise Exception("Scene could not be updated.")

                if not send_simple_message(TOPIC_ANNOTATION_MODIFIED, scene_id, {'status': 'updated'}):
                    raise Exception("Failed to send to Kafka")

                return {'message': "Scene updated", id_key: id_value}, 201

        except Exception as e:
            logger.error(f"Failed to update scene: {e}")
            return {'error': str(e)}, 500
