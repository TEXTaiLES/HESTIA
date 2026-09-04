import logging
import mimetypes

from pathlib import Path
from urllib.parse import quote
from flask import request
from flask_restful import Resource
from minio.error import S3Error

from middleware.security import require_api_key
from services.utils import stream_object, upload_filestorage
from services.storage import MINIO_ROBOT_BUCKET, build_public_url, minio_client


logger = logging.getLogger(__name__)

MULTISPECTRAL_IMAGE_PREFIX = "multispectral/"
SUPPORTED_EXTENSIONS = {".bmp", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


# ==============================================================================
# UTILS
# ==============================================================================


def _to_url_path(path: str) -> str:
    """Return a stable slash-separated path for API identifiers."""
    return path.replace("\\", "/").strip("/")


def _clean_dataset_name(image_name: str) -> str | None:
    """Return a safe multispectral dataset name."""
    clean_parts = [
        part
        for part in Path(_to_url_path(str(image_name or ""))).parts
        if part not in {"", ".", ".."}
    ]
    if not clean_parts:
        return None

    return "/".join(clean_parts)


def _clean_band_name(band: str) -> str | None:
    """Return a safe multispectral band filename."""
    clean_name = Path(str(band or "")).name
    if not clean_name or Path(clean_name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        return None

    return clean_name


def _get_dataset_prefix(image_name: str) -> str | None:
    """Return the MinIO object prefix for a multispectral dataset."""
    clean_name = _clean_dataset_name(image_name)
    if clean_name is None:
        return None

    return f"{MULTISPECTRAL_IMAGE_PREFIX}{clean_name}/"


def _get_band_key(path: str) -> str:
    """Return THOTH-facing wavelength key for a band image."""
    stem = Path(path).stem.lower()
    if stem == "rgb":
        return "rgb"
    if stem.isdigit():
        return f"{stem}nm"

    return stem


def _iter_band_objects(dataset_prefix: str) -> list[str]:
    """Return supported band object names in a multispectral dataset prefix."""
    objects = minio_client.list_objects(
        MINIO_ROBOT_BUCKET,
        prefix=dataset_prefix,
        recursive=True,
    )
    return sorted(
        obj.object_name
        for obj in objects
        if obj.object_name
        and Path(obj.object_name).suffix.lower() in SUPPORTED_EXTENSIONS
    )


def _iter_dataset_names() -> list[str]:
    """Return multispectral dataset names stored in MinIO."""
    dataset_names: set[str] = set()

    objects = minio_client.list_objects(
        MINIO_ROBOT_BUCKET,
        prefix=MULTISPECTRAL_IMAGE_PREFIX,
        recursive=True,
    )
    for obj in objects:
        object_name = obj.object_name or ""
        if Path(object_name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        relative_name = object_name.removeprefix(MULTISPECTRAL_IMAGE_PREFIX)
        dataset_name = str(Path(relative_name).parent).replace("\\", "/")
        if dataset_name and dataset_name != ".":
            dataset_names.add(dataset_name)

    return sorted(dataset_names)


def _build_file_url(image_name: str, band_file: str) -> str:
    """Return the API file URL for one multispectral band."""
    return (
        request.host_url.rstrip("/") +
        "/multispectral/file" +
        f"?image_name={quote(image_name)}&band={quote(band_file)}"
    )


def _build_descriptor(image_name: str, object_names: list[str]) -> dict:
    """Return a THOTH multispectral image descriptor."""
    dataset_prefix = _get_dataset_prefix(image_name)
    api_urls = {
        _get_band_key(object_name): _build_file_url(
            image_name,
            object_name.removeprefix(dataset_prefix),
        )
        for object_name in object_names
        if dataset_prefix is not None
    }
    storage_urls = {
        _get_band_key(object_name): build_public_url(MINIO_ROBOT_BUCKET, object_name)
        for object_name in object_names
    }

    return {
        "image_name": image_name,
        "image_url": storage_urls,
        "urls": storage_urls,
        "storage_urls": storage_urls,
        "api_urls": api_urls,
        "location": f"s3://{MINIO_ROBOT_BUCKET}/{dataset_prefix}",
        "description": None,
    }


def _get_descriptor(image_name: str) -> dict | None:
    """Return one multispectral descriptor from MinIO."""
    dataset_name = _clean_dataset_name(image_name)
    if dataset_name is None:
        return None

    dataset_prefix = _get_dataset_prefix(dataset_name)
    band_objects = _iter_band_objects(dataset_prefix)
    if not band_objects:
        return None

    return _build_descriptor(dataset_name, band_objects)


def _upload_multispectral_file(file, image_name: str) -> dict[str, str]:
    """Upload one multispectral band file to MinIO."""
    dataset_name = _clean_dataset_name(image_name)
    band_name = _clean_band_name(file.filename)
    if dataset_name is None:
        raise ValueError("Missing image_name")
    if band_name is None:
        raise ValueError(f"Unsupported band filename '{file.filename}'")

    object_name = f"{MULTISPECTRAL_IMAGE_PREFIX}{dataset_name}/{band_name}"
    upload_filestorage(MINIO_ROBOT_BUCKET, object_name, file)

    return {
        "band": band_name,
        "location": f"s3://{MINIO_ROBOT_BUCKET}/{object_name}",
        "public_url": build_public_url(MINIO_ROBOT_BUCKET, object_name),
    }


# ==============================================================================
# ENDPOINTS
# ==============================================================================


class MultispectralImageListResource(Resource):
    method_decorators = [require_api_key]

    def get(self):
        """Return available multispectral image descriptors."""
        try:
            return [
                descriptor
                for dataset_name in _iter_dataset_names()
                if (descriptor := _get_descriptor(dataset_name)) is not None
            ], 200
        except S3Error as exc:
            logger.error("Failed to list multispectral images from MinIO: %s", exc)
            return [], 200


class MultispectralImageResource(Resource):
    method_decorators = [require_api_key]

    def get(self, image_name: str | None = None):
        """Return a multispectral image descriptor."""
        image_name = image_name or request.args.get("image_name")
        if not image_name:
            return {"error": "Missing image_name"}, 400

        try:
            descriptor = _get_descriptor(image_name)
        except S3Error as exc:
            logger.error(
                "Failed to retrieve multispectral image '%s': %s",
                image_name,
                exc,
            )
            descriptor = None

        if descriptor is None:
            return {"error": f"Multispectral image '{image_name}' not found"}, 404

        return descriptor, 200

    def post(self, image_name: str | None = None):
        """Upload one multispectral dataset to MinIO."""
        image_name = (
            image_name or
            request.form.get("image_name") or
            request.args.get("image_name")
        )
        files = request.files.getlist("file")
        if not image_name:
            return {"error": "Missing image_name"}, 400
        if not files or files[0].filename == "":
            return {"error": "No file(s) provided."}, 400

        uploaded_bands = []
        for file in files:
            try:
                uploaded_bands.append(_upload_multispectral_file(file, image_name))
            except ValueError as exc:
                return {"error": str(exc)}, 400
            except Exception as exc:
                logger.error(
                    "Failed to upload multispectral band '%s': %s",
                    file.filename,
                    exc,
                )
                return {"error": f"Failed to upload band '{file.filename}'"}, 500

        descriptor = _get_descriptor(image_name)
        return {
            "message": f"Successfully processed {len(uploaded_bands)} file(s).",
            "uploaded_files": uploaded_bands,
            "image": descriptor,
        }, 201


class MultispectralImageFileResource(Resource):
    method_decorators = [require_api_key]

    def get(self):
        """Return multispectral band image bytes from MinIO."""
        image_name = request.args.get("image_name")
        band = request.args.get("band")
        if not image_name:
            return {"error": "Missing image_name"}, 400
        if not band:
            return {"error": "Missing band"}, 400

        dataset_prefix = _get_dataset_prefix(image_name)
        band_name = _clean_band_name(band)
        if dataset_prefix is None or band_name is None:
            return {"error": f"Band '{band}' not found"}, 404

        object_name = f"{dataset_prefix}{band_name}"
        mimetype = mimetypes.guess_type(band_name)[0] or "application/octet-stream"
        try:
            return stream_object(
                MINIO_ROBOT_BUCKET,
                object_name,
                download_name=band_name,
                mimetype=mimetype,
                as_attachment=False,
            )
        except Exception as exc:
            logger.error(
                "Failed to retrieve multispectral band '%s' from '%s': %s",
                band,
                image_name,
                exc,
            )
            return {"error": f"Band '{band}' not found"}, 404
