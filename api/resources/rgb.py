import logging
import mimetypes
from pathlib import Path

from flask import request
from flask_restful import Resource
from minio.error import S3Error

from middleware.security import require_api_key
from services.utils import stream_object, upload_filestorage
from services.storage import MINIO_ROBOT_BUCKET, build_public_url, minio_client


logger = logging.getLogger(__name__)


RGB_IMAGE_PREFIX = "rgb/"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


# ==============================================================================
# UTILS
# ==============================================================================

def _clean_image_name(image_name: str) -> str | None:
    """Return a safe RGB image object name."""
    clean_name = Path(str(image_name or "")).name
    if not clean_name or Path(clean_name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        return None

    return clean_name


def _get_object_name(image_name: str) -> str | None:
    """Return the MinIO object name for an RGB image."""
    clean_name = _clean_image_name(image_name)
    if clean_name is None:
        return None

    return f"{RGB_IMAGE_PREFIX}{clean_name}"


def _object_exists(object_name: str) -> bool:
    """Return whether an RGB image exists in MinIO."""
    try:
        minio_client.stat_object(MINIO_ROBOT_BUCKET, object_name)
        return True
    except S3Error:
        return False


def _build_descriptor(object_name: str) -> dict[str, str | None]:
    """Return an RGB image descriptor."""
    image_name = object_name.removeprefix(RGB_IMAGE_PREFIX)
    return {
        "image_name": image_name,
        "image_url": build_public_url(MINIO_ROBOT_BUCKET, object_name),
        "location": f"s3://{MINIO_ROBOT_BUCKET}/{object_name}",
        "description": None,
    }


def _list_rgb_images() -> list[dict[str, str | None]]:
    """Return RGB image descriptors stored in MinIO."""
    try:
        objects = minio_client.list_objects(
            MINIO_ROBOT_BUCKET,
            prefix=RGB_IMAGE_PREFIX,
            recursive=True,
        )
        return [
            _build_descriptor(obj.object_name)
            for obj in objects
            if obj.object_name
            and Path(obj.object_name).suffix.lower() in SUPPORTED_EXTENSIONS
        ]
    except S3Error as exc:
        logger.error("Failed to list RGB images from MinIO: %s", exc)
        return []


def _upload_rgb_file(file) -> dict[str, str | None]:
    """Upload one RGB image to MinIO and return its descriptor."""
    image_name = _clean_image_name(file.filename)
    if image_name is None:
        raise ValueError(f"Unsupported RGB image filename '{file.filename}'")

    object_name = f"{RGB_IMAGE_PREFIX}{image_name}"
    upload_filestorage(MINIO_ROBOT_BUCKET, object_name, file)

    return _build_descriptor(object_name)


# ==============================================================================
# ENDPOINTS
# ==============================================================================


class RgbImageListResource(Resource):
    method_decorators = [require_api_key]

    def get(self):
        """Return available RGB image summaries."""
        return _list_rgb_images(), 200


class RgbImageResource(Resource):
    method_decorators = [require_api_key]

    def get(self, image_name: str | None = None):
        """Return an RGB image descriptor."""
        image_name = image_name or request.args.get("image_name")
        if not image_name:
            return {"error": "Missing image_name"}, 400

        object_name = _get_object_name(image_name)
        if object_name is None or not _object_exists(object_name):
            return {"error": f"RGB image '{image_name}' not found"}, 404

        return _build_descriptor(object_name), 200

    def post(self, image_name: str | None = None):
        """Upload one or more RGB images to MinIO."""
        files = request.files.getlist("file")
        if not files or files[0].filename == "":
            return {"error": "No file(s) provided."}, 400

        uploaded_images = []
        for file in files:
            try:
                uploaded_images.append(_upload_rgb_file(file))
            except ValueError as exc:
                return {"error": str(exc)}, 400
            except Exception as exc:
                logger.error("Failed to upload RGB image '%s': %s", file.filename, exc)
                return {"error": f"Failed to upload RGB image '{file.filename}'"}, 500

        return {
            "message": f"Successfully processed {len(uploaded_images)} file(s).",
            "uploaded_files": uploaded_images,
        }, 201


class RgbImageFileResource(Resource):
    method_decorators = [require_api_key]

    def get(self, image_name: str):
        """Return RGB image bytes from MinIO."""
        object_name = _get_object_name(image_name)
        if object_name is None:
            return {"error": f"RGB image '{image_name}' not found"}, 404

        mimetype = mimetypes.guess_type(image_name)[0] or "application/octet-stream"
        try:
            return stream_object(
                MINIO_ROBOT_BUCKET,
                object_name,
                download_name=Path(image_name).name,
                mimetype=mimetype,
                as_attachment=False,
            )
        except Exception as exc:
            logger.error("Failed to retrieve RGB image '%s': %s", image_name, exc)
            return {"error": f"RGB image '{image_name}' not found"}, 404
