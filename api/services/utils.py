"""Shared helpers for the API.

Two groups live here: the OBJ -> GLB mesh conversion used by the reconstruction
pipeline, and the row/transfer helpers every resource shares — psycopg2 rows
turned into JSON-safe dicts, and file transfers that stream to/from MinIO
instead of buffering whole uploads (datasets, model weights, GLB models) in
memory.
"""
import logging
import os
import subprocess # without letting trimesh crash the main Flask process
import sys
import uuid
from datetime import datetime, date

from flask import Response, stream_with_context

from services.storage import minio_client

logger = logging.getLogger(__name__)


# ==============================================================================
# MESH CONVERSION
# ==============================================================================

def convert_obj_to_glb(input_path: str, output_path: str) -> bool:
    """
    Converts an OBJ file to a Draco-compressed GLB file.
    Runs in a subprocess to isolate crashes from the Flask process.
    """
    converter_script = os.path.join(os.path.dirname(__file__), '_obj_converter.py')

    # Write the converter script inline if it doesn't exist
    if not os.path.exists(converter_script):
        script = """
import sys
import trimesh

input_path, output_path = sys.argv[1], sys.argv[2]
try:
    scene = trimesh.load(input_path, process=True)
    if isinstance(scene, trimesh.Trimesh):
        import trimesh
        scene = trimesh.Scene(scene)
    scene.export(output_path, file_type='glb', extension_draco=True)
    if __import__('os').path.getsize(output_path) > 0:
        sys.exit(0)
    sys.exit(1)
except Exception as e:
    print(f"Conversion error: {e}", file=sys.stderr)
    sys.exit(1)
"""
        with open(converter_script, 'w') as f:
            f.write(script)

    try:
        logger.info(f"Converting {input_path} to GLB (subprocess)...")
        result = subprocess.run(
            [sys.executable, converter_script, input_path, output_path],
            timeout=120,
            capture_output=True,
            text=True
        )
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.info(f"GLB saved to {output_path} ({os.path.getsize(output_path)} bytes)")
            return True
        else:
            logger.error(f"GLB conversion failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("GLB conversion timed out after 120s")
        return False
    except Exception as e:
        logger.error(f"Failed to convert OBJ to GLB: {e}")
        return False


# ==============================================================================
# JSON-SAFE DATABASE ROWS
# ==============================================================================

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
    """Return every remaining row of an already-executed cursor as JSON-safe dicts."""
    if not cur.description:
        return []
    cols = [c[0] for c in cur.description]
    return [row_to_dict(cols, r) for r in cur.fetchall()]


def fetch_one_dict(cur):
    """Return the next row of an already-executed cursor as a JSON-safe dict."""
    row = cur.fetchone()
    if row is None:
        return None
    return row_to_dict([c[0] for c in cur.description], row)


# ==============================================================================
# MINIO TRANSFERS
# ==============================================================================

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


def stream_object(bucket, object_name, download_name=None,
                  mimetype='application/octet-stream', as_attachment=True):
    """Stream a MinIO object back to the client (chunked, no full read in memory).

    flask_restful passes a raw Werkzeug ``Response`` straight through, so this
    can be returned directly from a Resource method.

    ``as_attachment=False`` serves the object inline instead, which is what the
    image/proxy endpoints want so browsers render rather than download it.

    Note the object is fetched lazily: a missing object raises ``S3Error`` here
    (callers turn that into a 404), but a transport failure *during* streaming
    happens after the 200 and its headers are already on the wire.
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
        disposition = 'attachment' if as_attachment else 'inline'
        headers['Content-Disposition'] = f'{disposition}; filename="{download_name}"'
    return Response(stream_with_context(generate()), mimetype=mimetype, headers=headers)
