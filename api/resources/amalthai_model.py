"""AmalthAI trained-model registry + weights/config blob storage.

Append-only registry (each training run is a distinct model). Weights and config
blobs live in the ``amalthai-models`` bucket; metadata in ``amalthai_models``.
"""
from flask import request, jsonify
from flask_restful import Resource
from minio.error import S3Error
import uuid
import json
import logging

from middleware.security import require_api_key
from services.database import get_db_connection
from services.storage import build_public_url, MINIO_AMALTHAI_MODELS_BUCKET
from resources.amalthai_common import (
    rows_to_dicts,
    fetch_one_dict,
    upload_filestorage,
    stream_object,
)

logger = logging.getLogger(__name__)


def _fetch_model(cur, model_id):
    cur.execute("SELECT * FROM amalthai_models WHERE model_id = %s", (model_id,))
    return fetch_one_dict(cur)


def _store_model_blob(model_id, key_col, url_col, set_hash):
    """Shared upload handler for weights and config blobs."""
    files = request.files.getlist('file')
    if not files or files[0].filename == '':
        return {'error': "No file provided"}, 400
    f = files[0]
    object_name = f"{model_id}/{f.filename}"
    try:
        size = upload_filestorage(MINIO_AMALTHAI_MODELS_BUCKET, object_name, f)
        url = build_public_url(MINIO_AMALTHAI_MODELS_BUCKET, object_name)
        if set_hash:
            sql = (f"UPDATE amalthai_models SET {key_col}=%s, {url_col}=%s, "
                   "content_hash=COALESCE(%s, content_hash), updated_at=now() "
                   "WHERE model_id=%s RETURNING model_id")
            params = (object_name, url, request.form.get('content_hash'), model_id)
        else:
            sql = (f"UPDATE amalthai_models SET {key_col}=%s, {url_col}=%s, "
                   "updated_at=now() WHERE model_id=%s RETURNING model_id")
            params = (object_name, url, model_id)
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.fetchone() is None:
                return {'error': f"model {model_id} not found"}, 404
        return {'message': f"{key_col.split('_')[0]} stored",
                key_col: object_name, url_col: url, 'size_bytes': size}, 200
    except Exception as e:
        logger.error(f"amalthai model blob upload failed: {e}")
        return {'error': str(e)}, 500


def _download_model_blob(model_id, key_col):
    with get_db_connection() as conn, conn.cursor() as cur:
        row = _fetch_model(cur, model_id)
    if row is None or not row.get(key_col):
        return {'error': f"{key_col} not found for model {model_id}"}, 404
    object_name = row[key_col]
    try:
        return stream_object(MINIO_AMALTHAI_MODELS_BUCKET, object_name,
                             download_name=object_name.split('/')[-1])
    except S3Error:
        return {'error': "object missing in storage"}, 404


class AmalthaiModelResource(Resource):
    method_decorators = [require_api_key]

    def post(self):
        data = request.get_json(silent=True) or {}
        owner_slug = data.get('owner_slug')
        name = data.get('name')
        mode = data.get('mode')
        if not owner_slug or not name or not mode:
            return {'error': "owner_slug, name and mode are required"}, 400

        model_id = str(uuid.uuid4())
        extra = data.get('extra')
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO amalthai_models
                       (model_id, owner_slug, owner_email, name, mode, trained_on,
                        dataset_id, experiment_id, score, metric_name, trained_date,
                        content_hash, extra, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING model_id""",
                    (model_id, owner_slug, data.get('owner_email'), name, mode,
                     data.get('trained_on'), data.get('dataset_id'),
                     data.get('experiment_id'), data.get('score'),
                     data.get('metric_name'), data.get('trained_date'),
                     data.get('content_hash'),
                     json.dumps(extra) if extra is not None else None,
                     data.get('status', 'ready')),
                )
                row = _fetch_model(cur, model_id)
            return {'message': "model registered", 'model_id': model_id, 'data': row}, 201
        except Exception as e:
            logger.error(f"amalthai model create failed: {e}")
            return {'error': str(e)}, 500

    def get(self):
        owner_slug = request.args.get('owner_slug')
        if not owner_slug:
            return {'error': "owner_slug is required"}, 400
        mode = request.args.get('mode')
        sql, params = "SELECT * FROM amalthai_models WHERE owner_slug = %s", [owner_slug]
        if mode:
            sql += " AND mode = %s"; params.append(mode)
        # Deterministic order so a client that needs a positional id is stable.
        sql += " ORDER BY created_at, model_id"
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = rows_to_dicts(cur)
        return jsonify(rows)


class AmalthaiModelItemResource(Resource):
    method_decorators = [require_api_key]

    def get(self, model_id):
        with get_db_connection() as conn, conn.cursor() as cur:
            row = _fetch_model(cur, model_id)
        if row is None:
            return {'error': f"model {model_id} not found"}, 404
        return jsonify(row)


class AmalthaiModelWeightsResource(Resource):
    method_decorators = [require_api_key]

    def post(self, model_id):
        return _store_model_blob(model_id, 'weights_key', 'weights_url', set_hash=True)

    def get(self, model_id):
        return _download_model_blob(model_id, 'weights_key')


class AmalthaiModelConfigResource(Resource):
    method_decorators = [require_api_key]

    def post(self, model_id):
        return _store_model_blob(model_id, 'config_key', 'config_url', set_hash=False)

    def get(self, model_id):
        return _download_model_blob(model_id, 'config_key')
