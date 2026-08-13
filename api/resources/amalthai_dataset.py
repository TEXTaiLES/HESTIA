"""AmalthAI dataset registry + canonical-archive storage.

Mutable registry rows (direct psycopg2, like nefele_jobs). The dataset itself is
stored as a single canonical archive blob in the ``amalthai-datasets`` bucket;
metadata + a manifest live in the ``amalthai_datasets`` table.
"""
from flask import request, jsonify
from minio.error import S3Error
import uuid
import json
import logging

from services.database import get_db_connection
from resources.resource_base import ResourceBase, BadRequest
from services.storage import build_public_url, MINIO_AMALTHAI_DATASETS_BUCKET
from resources.amalthai_common import (
    fetch_one_dict,
    upload_filestorage,
    stream_object,
)

logger = logging.getLogger(__name__)


def _fetch_dataset(cur, dataset_id):
    cur.execute("SELECT * FROM amalthai_datasets WHERE dataset_id = %s", (dataset_id,))
    return fetch_one_dict(cur)


class AmalthaiDatasetResource(ResourceBase):
    table = "amalthai_datasets"
    order_by = "created_at DESC"
    per_page_default = 100

    def post(self):
        """Register (or upsert) a dataset by (owner_slug, mode, name)."""
        data = request.get_json(silent=True) or {}
        owner_slug = data.get('owner_slug')
        name = data.get('name')
        mode = data.get('mode')
        if not owner_slug or not name or not mode:
            return {'error': "owner_slug, name and mode are required"}, 400

        dataset_id = str(uuid.uuid4())
        manifest = data.get('manifest')
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO amalthai_datasets
                       (dataset_id, owner_slug, owner_email, name, mode, num_classes,
                        content_hash, manifest, linked_scan_id, linked_artifact_id,
                        linked_reconstruction_id, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (owner_slug, mode, name) DO UPDATE SET
                           num_classes = EXCLUDED.num_classes,
                           content_hash = EXCLUDED.content_hash,
                           manifest = EXCLUDED.manifest,
                           owner_email = EXCLUDED.owner_email,
                           linked_scan_id = EXCLUDED.linked_scan_id,
                           linked_artifact_id = EXCLUDED.linked_artifact_id,
                           linked_reconstruction_id = EXCLUDED.linked_reconstruction_id,
                           updated_at = now()
                       RETURNING dataset_id""",
                    (dataset_id, owner_slug, data.get('owner_email'), name, mode,
                     data.get('num_classes'), data.get('content_hash'),
                     json.dumps(manifest) if manifest is not None else None,
                     data.get('linked_scan_id'), data.get('linked_artifact_id'),
                     data.get('linked_reconstruction_id'), data.get('status', 'ready')),
                )
                resolved_id = cur.fetchone()[0]
                row = _fetch_dataset(cur, resolved_id)
            return {'message': "dataset registered",
                    'dataset_id': str(resolved_id), 'data': row}, 201
        except Exception as e:
            logger.error(f"amalthai dataset create failed: {e}")
            return {'error': str(e)}, 500

    def build_GET_conditions(self):
        owner_slug = request.args.get('owner_slug')
        if not owner_slug:
            raise BadRequest('owner_slug is required')

        conditions = [('owner_slug', '=', owner_slug)]
        if mode := request.args.get('mode'):
            conditions.append(('mode', '=', mode))
        if name := request.args.get('name'):
            conditions.append(('name', '=', name))
        return conditions


class AmalthaiDatasetItemResource(ResourceBase):
    table = "amalthai_datasets"

    def get(self, dataset_id):
        with get_db_connection() as conn, conn.cursor() as cur:
            row = _fetch_dataset(cur, dataset_id)
        if row is None:
            return {'error': f"dataset {dataset_id} not found"}, 404
        return jsonify(row)


class AmalthaiDatasetArchiveResource(ResourceBase):
    table = "amalthai_datasets"

    def post(self, dataset_id):
        """Upload the canonical dataset archive blob (tar.gz/zip)."""
        files = request.files.getlist('file')
        if not files or files[0].filename == '':
            return {'error': "No file provided"}, 400
        f = files[0]
        object_name = f"{dataset_id}/{f.filename}"
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                if _fetch_dataset(cur, dataset_id) is None:
                    return {'error': f"dataset {dataset_id} not found"}, 404

            size = upload_filestorage(MINIO_AMALTHAI_DATASETS_BUCKET, object_name, f)
            archive_url = build_public_url(MINIO_AMALTHAI_DATASETS_BUCKET, object_name)

            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE amalthai_datasets
                       SET object_key=%s, archive_url=%s, size_bytes=%s,
                           content_hash=COALESCE(%s, content_hash),
                           status='ready', updated_at=now()
                       WHERE dataset_id=%s RETURNING dataset_id""",
                    (object_name, archive_url, size,
                     request.form.get('content_hash'), dataset_id),
                )
                if cur.fetchone() is None:
                    return {'error': f"dataset {dataset_id} not found"}, 404
            return {'message': "archive stored", 'object_key': object_name,
                    'archive_url': archive_url, 'size_bytes': size}, 200
        except Exception as e:
            logger.error(f"amalthai dataset archive upload failed: {e}")
            return {'error': str(e)}, 500

    def get(self, dataset_id):
        """Download the canonical dataset archive (streamed)."""
        with get_db_connection() as conn, conn.cursor() as cur:
            row = _fetch_dataset(cur, dataset_id)
        if row is None or not row.get('object_key'):
            return {'error': f"archive for dataset {dataset_id} not found"}, 404
        object_name = row['object_key']
        try:
            return stream_object(MINIO_AMALTHAI_DATASETS_BUCKET, object_name,
                                 download_name=object_name.split('/')[-1])
        except S3Error:
            return {'error': "archive object missing in storage"}, 404
