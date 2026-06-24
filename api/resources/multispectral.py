from pathlib import Path
from urllib.parse import quote

from flask import request, send_from_directory
from flask_restful import Resource

from middleware.security import require_api_key


MULTISPECTRAL_IMAGE_DIR = "I don't know how to configure this"
SUPPORTED_EXTENSIONS = {".bmp", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}



def _to_url_path(path: Path) -> str:
    """Return a stable slash-separated path for API identifiers."""
    return path.as_posix()


def _get_dataset_dir(image_name: str) -> Path | None:
    """Return a safe multispectral dataset directory."""
    if not image_name:
        return None

    clean_parts = [
        part
        for part in Path(image_name.replace("\\", "/")).parts
        if part not in {"", ".", ".."}
    ]
    if not clean_parts:
        return None

    dataset_dir = MULTISPECTRAL_IMAGE_DIR.joinpath(*clean_parts)
    try:
        dataset_dir.relative_to(MULTISPECTRAL_IMAGE_DIR)
    except ValueError:
        return None

    if dataset_dir.is_dir() and _get_band_files(dataset_dir):
        return dataset_dir

    return None


def _get_band_files(dataset_dir: Path) -> list[Path]:
    """Return supported band files in a multispectral dataset directory."""
    return sorted(
        path
        for path in dataset_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def _get_band_key(path: Path) -> str:
    """Return THOTH-facing wavelength key for a band image."""
    stem = path.stem.lower()
    if stem == "rgb":
        return "rgb"
    if stem.isdigit():
        return f"{stem}nm"

    return stem


def _iter_dataset_dirs() -> list[Path]:
    """Return all folders that directly contain multispectral band files."""
    if not MULTISPECTRAL_IMAGE_DIR.exists():
        return []

    return sorted(
        path
        for path in MULTISPECTRAL_IMAGE_DIR.rglob("*")
        if path.is_dir() and _get_band_files(path)
    )


def _build_file_url(image_name: str, band_file: str) -> str:
    """Return a public file URL for one multispectral band."""
    return (
        request.host_url.rstrip("/") +
        "/multispectral/file" +
        f"?image_name={quote(image_name)}&band={quote(band_file)}"
    )


def _build_descriptor(dataset_dir: Path) -> dict:
    """Return a THOTH multispectral image descriptor."""
    image_name = _to_url_path(dataset_dir.relative_to(MULTISPECTRAL_IMAGE_DIR))
    image_url = {
        _get_band_key(path): _build_file_url(image_name, path.name)
        for path in _get_band_files(dataset_dir)
    }

    return {
        "image_name": image_name,
        "image_url": image_url,
        "urls": image_url,
        "description": None,
    }


class MultispectralImageListResource(Resource):
    method_decorators = [require_api_key]

    def get(self):
        """Return available multispectral image descriptors."""
        return [
            _build_descriptor(path)
            for path in _iter_dataset_dirs()
        ], 200


class MultispectralImageResource(Resource):
    method_decorators = [require_api_key]

    def get(self, image_name: str | None = None):
        """Return a multispectral image descriptor."""
        image_name = image_name or request.args.get("image_name")
        if not image_name:
            return {"error": "Missing image_name"}, 400

        dataset_dir = _get_dataset_dir(image_name)
        if not dataset_dir:
            return {"error": f"Multispectral image '{image_name}' not found"}, 404

        return _build_descriptor(dataset_dir), 200


class MultispectralImageFileResource(Resource):
    def get(self):
        """Return multispectral band image bytes."""
        image_name = request.args.get("image_name")
        band = request.args.get("band")
        if not image_name:
            return {"error": "Missing image_name"}, 400
        if not band:
            return {"error": "Missing band"}, 400

        dataset_dir = _get_dataset_dir(image_name)
        if not dataset_dir:
            return {"error": f"Multispectral image '{image_name}' not found"}, 404

        band_name = Path(band).name
        band_path = dataset_dir / band_name
        if band_path not in _get_band_files(dataset_dir):
            return {"error": f"Band '{band}' not found"}, 404

        return send_from_directory(
            dataset_dir,
            band_name,
            as_attachment=False,
        )
