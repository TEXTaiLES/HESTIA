"""AmalthAI training-run (experiment) records.

One row per train submit; captures params + instructions and is PATCHed with
status/metrics/result_model_id as training progresses (worker-style, like the
nefele PATCH path).
"""
from flask import request, jsonify
from flask_restful import Resource
import uuid
import json
import logging

from middleware.security import require_api_key
from services.database import get_db_connection
from services.utils import rows_to_dicts, fetch_one_dict

logger = logging.getLogger(__name__)


def _fetch_experiment(cur, experiment_id):
    cur.execute("SELECT * FROM amalthai_experiments WHERE experiment_id = %s", (experiment_id,))
    return fetch_one_dict(cur)


class AmalthaiExperimentResource(Resource):
    method_decorators = [require_api_key]

    def post(self):
        data = request.get_json(silent=True) or {}
        owner_slug = data.get('owner_slug')
        mode = data.get('mode')
        if not owner_slug or not mode:
            return {'error': "owner_slug and mode are required"}, 400

        experiment_id = str(uuid.uuid4())
        params = data.get('params')
        instructions = data.get('instructions')
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO amalthai_experiments
                       (experiment_id, owner_slug, owner_email, mode, dataset_id,
                        dataset_name, requested_model, params, instructions, job_id, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING experiment_id""",
                    (experiment_id, owner_slug, data.get('owner_email'), mode,
                     data.get('dataset_id'), data.get('dataset_name'),
                     data.get('requested_model'),
                     json.dumps(params) if params is not None else None,
                     json.dumps(instructions) if instructions is not None else None,
                     data.get('job_id'), data.get('status', 'submitted')),
                )
                row = _fetch_experiment(cur, experiment_id)
            return {'message': "experiment created",
                    'experiment_id': experiment_id, 'data': row}, 201
        except Exception as e:
            logger.error(f"amalthai experiment create failed: {e}")
            return {'error': str(e)}, 500

    def get(self):
        owner_slug = request.args.get('owner_slug')
        if not owner_slug:
            return {'error': "owner_slug is required"}, 400
        mode = request.args.get('mode')
        status = request.args.get('status')
        sql, params = "SELECT * FROM amalthai_experiments WHERE owner_slug = %s", [owner_slug]
        if mode:
            sql += " AND mode = %s"; params.append(mode)
        if status:
            sql += " AND status = %s"; params.append(status)
        sql += " ORDER BY created_at DESC"
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = rows_to_dicts(cur)
        return jsonify(rows)


class AmalthaiExperimentItemResource(Resource):
    method_decorators = [require_api_key]

    def get(self, experiment_id):
        with get_db_connection() as conn, conn.cursor() as cur:
            row = _fetch_experiment(cur, experiment_id)
        if row is None:
            return {'error': f"experiment {experiment_id} not found"}, 404
        return jsonify(row)

    def patch(self, experiment_id):
        data = request.get_json(silent=True) or {}
        sets, params = [], []
        for field in ('status', 'error', 'dataset_id', 'result_model_id', 'requested_model'):
            if field in data:
                sets.append(f"{field} = %s")
                params.append(data[field])
        for jfield in ('params', 'instructions', 'metrics'):
            if jfield in data:
                sets.append(f"{jfield} = %s")
                params.append(json.dumps(data[jfield]) if data[jfield] is not None else None)
        if not sets:
            return {'error': "no updatable fields provided"}, 400
        sets.append("updated_at = now()")
        params.append(experiment_id)
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    f"UPDATE amalthai_experiments SET {', '.join(sets)} "
                    "WHERE experiment_id = %s RETURNING experiment_id",
                    tuple(params),
                )
                if cur.fetchone() is None:
                    return {'error': f"experiment {experiment_id} not found"}, 404
            return {'message': "experiment updated", 'experiment_id': experiment_id}, 200
        except Exception as e:
            logger.error(f"amalthai experiment patch failed: {e}")
            return {'error': str(e)}, 500
