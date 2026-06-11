"""AmalthAI inference-run records + input/output image storage.

One row per inference POST. Input and output images are stored in the
``amalthai-inference`` bucket; their URLs live in the ``inputs``/``outputs``
JSONB columns (mirrors nefele's ``preview`` JSONB).
"""
from flask import request, jsonify
from flask_restful import Resource
import uuid
import json
import logging

from middleware.security import require_api_key
from services.database import get_db_connection
from services.storage import build_public_url, MINIO_AMALTHAI_INFERENCE_BUCKET
from resources.amalthai_common import (
    rows_to_dicts,
    fetch_one_dict,
    upload_filestorage,
)

logger = logging.getLogger(__name__)


def _fetch_inference(cur, inference_id):
    cur.execute("SELECT * FROM amalthai_inference_runs WHERE inference_id = %s", (inference_id,))
    return fetch_one_dict(cur)


class AmalthaiInferenceResource(Resource):
    method_decorators = [require_api_key]

    def post(self):
        data = request.get_json(silent=True) or {}
        owner_slug = data.get('owner_slug')
        mode = data.get('mode')
        if not owner_slug or not mode:
            return {'error': "owner_slug and mode are required"}, 400

        inference_id = str(uuid.uuid4())
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO amalthai_inference_runs
                       (inference_id, owner_slug, owner_email, mode, model_id,
                        model_name, dataset_name, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,'pending')
                       RETURNING inference_id""",
                    (inference_id, owner_slug, data.get('owner_email'), mode,
                     data.get('model_id'), data.get('model_name'), data.get('dataset_name')),
                )
                row = _fetch_inference(cur, inference_id)
            return {'message': "inference run created",
                    'inference_id': inference_id, 'data': row}, 201
        except Exception as e:
            logger.error(f"amalthai inference create failed: {e}")
            return {'error': str(e)}, 500

    def get(self):
        owner_slug = request.args.get('owner_slug')
        if not owner_slug:
            return {'error': "owner_slug is required"}, 400
        mode = request.args.get('mode')
        model_id = request.args.get('model_id')
        sql, params = "SELECT * FROM amalthai_inference_runs WHERE owner_slug = %s", [owner_slug]
        if mode:
            sql += " AND mode = %s"; params.append(mode)
        if model_id:
            sql += " AND model_id = %s"; params.append(model_id)
        sql += " ORDER BY created_at DESC"
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = rows_to_dicts(cur)
        return jsonify(rows)


class AmalthaiInferenceItemResource(Resource):
    method_decorators = [require_api_key]

    def get(self, inference_id):
        with get_db_connection() as conn, conn.cursor() as cur:
            row = _fetch_inference(cur, inference_id)
        if row is None:
            return {'error': f"inference run {inference_id} not found"}, 404
        return jsonify(row)


class AmalthaiInferenceInputsResource(Resource):
    method_decorators = [require_api_key]

    def post(self, inference_id):
        files = request.files.getlist('file')
        if not files or files[0].filename == '':
            return {'error': "No file(s) provided"}, 400
        items = []
        try:
            for f in files:
                object_name = f"{inference_id}/inputs/{f.filename}"
                upload_filestorage(MINIO_AMALTHAI_INFERENCE_BUCKET, object_name, f)
                items.append({
                    'filename': f.filename,
                    'key': object_name,
                    'url': build_public_url(MINIO_AMALTHAI_INFERENCE_BUCKET, object_name),
                })
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE amalthai_inference_runs
                       SET inputs=%s, input_prefix=%s, updated_at=now()
                       WHERE inference_id=%s RETURNING inference_id""",
                    (json.dumps(items), f"{inference_id}/inputs/", inference_id),
                )
                if cur.fetchone() is None:
                    return {'error': f"inference run {inference_id} not found"}, 404
            return {'message': "inputs stored", 'inputs': items}, 200
        except Exception as e:
            logger.error(f"amalthai inference inputs failed: {e}")
            return {'error': str(e)}, 500


class AmalthaiInferenceOutputsResource(Resource):
    method_decorators = [require_api_key]

    def post(self, inference_id):
        """Upload output image(s) and finalize the run.

        Optional form fields:
          - ``mapping``: JSON {output_filename: input_filename}
          - ``color_table``: JSON, stored under extra.color_table
        """
        files = request.files.getlist('file')
        if not files or files[0].filename == '':
            return {'error': "No file(s) provided"}, 400

        mapping = {}
        if 'mapping' in request.form:
            try:
                mapping = json.loads(request.form.get('mapping'))
            except json.JSONDecodeError:
                return {'error': "Invalid JSON in 'mapping'"}, 400

        extra = None
        if 'color_table' in request.form:
            try:
                extra = {'color_table': json.loads(request.form.get('color_table'))}
            except json.JSONDecodeError:
                extra = None

        items = []
        try:
            for f in files:
                object_name = f"{inference_id}/outputs/{f.filename}"
                upload_filestorage(MINIO_AMALTHAI_INFERENCE_BUCKET, object_name, f)
                items.append({
                    'filename': f.filename,
                    'key': object_name,
                    'url': build_public_url(MINIO_AMALTHAI_INFERENCE_BUCKET, object_name),
                    'input_filename': mapping.get(f.filename),
                })

            if extra is not None:
                sql = ("UPDATE amalthai_inference_runs "
                       "SET outputs=%s, output_prefix=%s, extra=%s, "
                       "status='completed', updated_at=now() "
                       "WHERE inference_id=%s RETURNING inference_id")
                params = (json.dumps(items), f"{inference_id}/outputs/",
                          json.dumps(extra), inference_id)
            else:
                sql = ("UPDATE amalthai_inference_runs "
                       "SET outputs=%s, output_prefix=%s, "
                       "status='completed', updated_at=now() "
                       "WHERE inference_id=%s RETURNING inference_id")
                params = (json.dumps(items), f"{inference_id}/outputs/", inference_id)

            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.fetchone() is None:
                    return {'error': f"inference run {inference_id} not found"}, 404
            return {'message': "outputs stored", 'outputs': items}, 200
        except Exception as e:
            logger.error(f"amalthai inference outputs failed: {e}")
            return {'error': str(e)}, 500
