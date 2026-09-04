from flask import request
import uuid
import json
import logging

from services.utils import fetch_one_dict, upload_filestorage
from resources.resource_base import ResourceBase
from services.database import get_db_connection
from services.storage import (
    build_public_url,
    MINIO_NEFELE_BUCKET,
)
from services.messaging import (
    send_simple_message,
    TOPIC_NEFELE_JOB_CREATED,
    TOPIC_NEFELE_JOB_MODIFIED
)

logger = logging.getLogger(__name__)


class NefeleResource(ResourceBase):
    # NOTE:  Missing avro_schema

    table = "nefele_jobs"
    order_by = "created_at DESC"

    def build_GET_conditions(self):
        conditions = []
        if status := request.args.get('status'):
            conditions.append(('status', '=', status))
        if scan_id := request.args.get('scan_id'):
            conditions.append(('scan_id', '=', scan_id))
        return conditions

    def post(self):
        data = request.get_json(silent=True) or {}
        scan_id = data.get('scan_id')
        dataset_name = data.get('dataset_name')
        model = data.get('model')

        # Validate
        if not scan_id or not dataset_name:
            return {'error': "scan_id and dataset_name are required"}, 400
        if model not in ('sugar', 'pgsr', 'fastpgsr'):
            model = 'sugar'

        conn = None
        job_id = str(uuid.uuid4())
        try:
            sql = """
                INSERT INTO nefele_jobs (job_id, scan_id, dataset_name, model, points_json, status, artifact_id)
                VALUES (%s, %s, %s, %s, %s, 'points_submitted', %s)
            """
            params = (
                job_id,
                scan_id,
                dataset_name,
                model,
                json.dumps(points_json) if (points_json := data.get('points_json')) else None,
                data.get('artifact_id'),
            )
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(sql, params)
                cur.execute("SELECT * FROM nefele_jobs WHERE job_id = %s", (job_id,))
                row = fetch_one_dict(cur)

            # Notify Listeners via Kafka
            notification = {'job_id': job_id, 'scan_id': scan_id}
            send_simple_message(TOPIC_NEFELE_JOB_CREATED, job_id, notification)

            return {'message': "nefele job created",
                    'job_id': job_id, 'data': row}, 201
        except Exception as e:
            logger.error(f"nefele create failed: {e}")
            return {'error': str(e)}, 500
        finally:
            if conn is not None:
                conn.close()


class NefeleJobResource(ResourceBase):
    # NOTE: Missing avro_schema

    table = "nefele_jobs"

    def get(self, job_id):
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM nefele_jobs WHERE job_id = %s", (job_id,))
            row = fetch_one_dict(cur)
        if row is None:
            return {'error': f"job {job_id} not found"}, 404
        return row, 200

    def patch(self, job_id):
        data = request.get_json(silent=True) or {}
        sets, params = [], []
        new_status = None

        # UI path — instructions -> derive status
        if 'instructions' in data:
            instr = data['instructions'] or {}
            decision = instr.get('decision')
            if decision not in ('confirm', 'redo', 'use_existing'):
                return {'error': f"bad decision: {decision}"}, 400
            sets.append("instructions = %s")
            params.append(json.dumps(instr))
            new_status = 'points_submitted' if decision == 'redo' else 'running'
            sets.append("status = %s")
            params.append(new_status)
        else:
            # Worker path — progress fields
            for field in ('stage', 'message', 'status', 'error'):
                if field in data:
                    sets.append(f"{field} = %s")
                    params.append(data[field])
                    if field == 'status':
                        new_status = data[field]
            if 'stage_index' in data:
                sets.append("stage_index = %s")
                params.append(int(data['stage_index']))

        if not sets:
            return {'error': "no updatable fields provided"}, 400

        sets.append("updated_at = now()")
        params.append(job_id)
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    f"UPDATE nefele_jobs SET {', '.join(sets)} "
                    f"WHERE job_id = %s RETURNING job_id",
                    tuple(params),
                )
                if cur.fetchone() is None:
                    return {'error': f"job {job_id} not found"}, 404

            send_simple_message(TOPIC_NEFELE_JOB_MODIFIED, job_id,
                                {'job_id': job_id, 'status': new_status})

            return {'message': "nefele job updated", 'job_id': job_id}, 200
        except Exception as e:
            logger.error(f"nefele patch failed: {e}")
            return {'error': str(e)}, 500


class NefeleClaimResource(ResourceBase):
    table = "nefele_jobs"

    def post(self):
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE nefele_jobs SET status = 'previewing', updated_at = now()
                    WHERE job_id = (
                        SELECT job_id FROM nefele_jobs
                        WHERE status = 'points_submitted'
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING *
                    """
                )
                result = fetch_one_dict(cur)
                if result is None:
                    return '', 204

            send_simple_message(TOPIC_NEFELE_JOB_MODIFIED, result['job_id'],
                                {'job_id': result['job_id'], 'status': 'previewing'})

            return result, 200
        except Exception as e:
            logger.error(f"nefele claim failed: {e}")
            return {'error': str(e)}, 500


class NefelePreviewResource(ResourceBase):
    table = "nefele_jobs"

    def post(self, job_id):
        files = request.files.getlist('file')
        if not files or files[0].filename == '':
            return {'error': "No preview file(s) provided."}, 400

        urls = []
        try:
            for f in files:
                object_name = f"{job_id}/{f.filename}"
                upload_filestorage(
                    MINIO_NEFELE_BUCKET, object_name, f,
                    content_type=f.content_type or 'image/png',
                )
                urls.append(build_public_url(MINIO_NEFELE_BUCKET, object_name))

            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE nefele_jobs
                       SET preview = %s, status = 'preview_ready', updated_at = now()
                       WHERE job_id = %s RETURNING job_id""",
                    (json.dumps(urls), job_id),
                )
                if cur.fetchone() is None:
                    return {'error': f"job {job_id} not found"}, 404

            send_simple_message(TOPIC_NEFELE_JOB_MODIFIED, job_id,
                                {'job_id': job_id, 'status': 'preview_ready'})

            return {'message': "preview stored", 'preview': urls}, 200
        except Exception as e:
            logger.error(f"nefele preview failed: {e}")
            return {'error': str(e)}, 500


class NefeleCancelResource(ResourceBase):
    table = "nefele_jobs"

    def post(self, job_id):
        """POST /nefele/<id>/cancel — atomic cancel of a non-terminal job.

        Sets status='cancelled' iff the job is currently in one of
        {points_submitted, previewing, preview_ready, running}. If the job
        already reached a terminal state, returns 409 to make the race visible.
        """
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE nefele_jobs
                       SET status = 'cancelled', updated_at = now()
                     WHERE job_id = %s
                       AND status IN ('points_submitted','previewing',
                                      'preview_ready','running')
                    RETURNING job_id
                    """,
                    (job_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return {'error': f"job {job_id} not cancellable "
                                     "(missing or terminal)"}, 409

            send_simple_message(TOPIC_NEFELE_JOB_MODIFIED, job_id,
                                {'job_id': job_id, 'status': 'cancelled'})
            return {'message': 'cancelled', 'job_id': job_id}, 200
        except Exception as e:
            logger.error(f"nefele cancel failed: {e}")
            return {'error': str(e)}, 500
