from pathlib import Path
from urllib.parse import quote

from flask import request, send_from_directory
from flask_restful import Resource

from middleware.security import require_api_key


RGB_IMAGE_DIR = "I don't know how to configure this"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _list_rgb_images() -> list[dict[str, str | None]]:
    """Return local RGB image descriptors."""
    if not RGB_IMAGE_DIR.exists():
        return []

    images = []
    for image_path in sorted(RGB_IMAGE_DIR.iterdir()):
        if image_path.is_file() and image_path.suffix.lower() in SUPPORTED_EXTENSIONS:
            images.append({
                "image_name": image_path.name,
                "description": None,
            })

    return images


def _get_image_path(image_name: str) -> Path | None:
    """Return a safe local image path by image name."""
    clean_name = Path(image_name).name
    image_path = RGB_IMAGE_DIR / clean_name
    if image_path.is_file() and image_path.suffix.lower() in SUPPORTED_EXTENSIONS:
        return image_path

    return None


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

        image_path = _get_image_path(image_name)
        if not image_path:
            return {"error": f"RGB image '{image_name}' not found"}, 404

        image_url = (
            request.host_url.rstrip("/") +
            f"/rgb/images/{quote(image_path.name)}/file"
        )

        return {
            "image_name": image_path.name,
            "image_url": image_url,
            "description": None,
        }, 200


class RgbImageFileResource(Resource):
    def get(self, image_name: str):
        """Return RGB image bytes."""
        image_path = _get_image_path(image_name)
        if not image_path:
            return {"error": f"RGB image '{image_name}' not found"}, 404

        return send_from_directory(
            RGB_IMAGE_DIR,
            image_path.name,
            as_attachment=False,
        )
