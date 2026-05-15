import logging
import os
import subprocess
import sys
import tempfile

from services.storage import minio_client, MINIO_RECONSTRUCTION_BUCKET

logger = logging.getLogger(__name__)

_THUMBNAIL_SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'scripts', 'generate_thumbnail.py')
)


def render_glb_thumbnail(glb_minio_path: str, view_index: int = 7) -> bytes | None:
    """
    Downloads a GLB from MinIO, renders a thumbnail via subprocess, returns PNG bytes.
    glb_minio_path: the object name inside MINIO_RECONSTRUCTION_BUCKET (e.g. "{id}/model.glb")
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        glb_local = os.path.join(tmp_dir, 'model.glb')
        png_local = os.path.join(tmp_dir, 'thumbnail.png')

        try:
            minio_client.fget_object(MINIO_RECONSTRUCTION_BUCKET, glb_minio_path, glb_local) # download GLB to local temp file
        except Exception as e:
            logger.error(f"Failed to download GLB from MinIO ({glb_minio_path}): {e}")
            return None

        env = {**os.environ, 'PYOPENGL_PLATFORM': 'egl', 'PYGLET_HEADLESS': '1', 'DISPLAY': ''}
        try:
            result = subprocess.run(
                [sys.executable, _THUMBNAIL_SCRIPT, glb_local, png_local, str(view_index)],
                timeout=60,
                capture_output=True,
                text=True,
                env=env
            )
            if result.returncode != 0 or not os.path.exists(png_local):
                logger.error(f"Thumbnail render failed: {result.stderr}")
                return None
        except subprocess.TimeoutExpired:
            logger.error("Thumbnail render timed out after 60s")
            return None
        except Exception as e:
            logger.error(f"Thumbnail render subprocess error: {e}")
            return None

        with open(png_local, 'rb') as f:
            return f.read()