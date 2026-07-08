"""Shared helpers for the AmalthAI integration resources.

These mirror the conventions in ``nefele_job.py`` (direct psycopg2, JSON-safe
rows) but are factored out here because the four ``amalthai_*`` resource modules
share them. File transfers stream to/from MinIO instead of buffering whole
datasets/model weights in memory.
"""
import os
import uuid
import logging
from datetime import datetime, date

from flask import Response, stream_with_context

from services.storage import minio_client

logger = logging.getLogger(__name__)


def _json_safe(value):
    """psycopg2 value -> JSON-safe (datetime -> isoformat, UUID -> str)."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def row_to_dict(colnames, row):
    return {col: _json_safe(val) for col, val in zip(colnames, row)}


def rows_to_dicts(cur):
    cols = [c[0] for c in cur.description]
    return [row_to_dict(cols, r) for r in cur.fetchall()]


def fetch_one_dict(cur):
    """Return the next row of an already-executed cursor as a JSON-safe dict."""
    row = cur.fetchone()
    if row is None:
        return None
    return row_to_dict([c[0] for c in cur.description], row)


def upload_filestorage(bucket, object_name, file_storage, content_type=None):
    """Stream a Werkzeug FileStorage to MinIO without buffering it in memory.

    Werkzeug spools large uploads to a temp file, so seeking the stream to
    measure its length is cheap and avoids ``file_storage.read()`` building a
    multi-hundred-MB bytes blob (datasets / model weights can be large).
    """
    stream = file_storage.stream
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    minio_client.put_object(
        bucket, object_name, stream, size,
        content_type=content_type or file_storage.content_type or 'application/octet-stream',
    )
    return size


def stream_object(bucket, object_name, download_name=None, mimetype='application/octet-stream'):
    """Stream a MinIO object back to the client (chunked, no full read in memory).

    flask_restful passes a raw Werkzeug ``Response`` straight through, so this
    can be returned directly from a Resource method (same as FileProxyResource).
    """
    response = minio_client.get_object(bucket, object_name)

    def generate():
        try:
            for chunk in response.stream(1024 * 1024):
                yield chunk
        finally:
            response.close()
            response.release_conn()

    headers = {}
    if download_name:
        headers['Content-Disposition'] = f'attachment; filename="{download_name}"'
    return Response(stream_with_context(generate()), mimetype=mimetype, headers=headers)
