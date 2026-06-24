import json
import re
from pathlib import Path

from flask import request
from flask_restful import Resource

from middleware.security import require_api_key


SCENE_DIR = "I don't know how to configure this"
SAFE_SCENE_ID_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


def _clean_scene_id(scene_id: str) -> str:
    """Return a filesystem-safe scene identifier."""
    clean_scene_id = SAFE_SCENE_ID_PATTERN.sub("_", str(scene_id or "").strip())
    return clean_scene_id.strip("._")


def _get_scene_path(scene_id: str) -> Path:
    """Return the local JSON path for a scene."""
    clean_scene_id = _clean_scene_id(scene_id)
    if not clean_scene_id:
        raise ValueError("Missing scene_id")

    return SCENE_DIR / f"{clean_scene_id}.json"


def _empty_metadata() -> dict:
    """Return an empty THOTH-compatible metadata object."""
    return {
        "schema": {
            "name": "",
            "version": "",
            "description": "",
            "url": "",
        },
        "attributes": {},
    }


def _empty_transform() -> dict:
    """Return a default THOTH-compatible transform object."""
    return {
        "translation": {
            "x": 0,
            "y": 0,
            "z": 0,
        },
        "rotation": {
            "x": 0,
            "y": 0,
            "z": 0,
        },
        "scale": {
            "x": 1,
            "y": 1,
            "z": 1,
        },
    }


def _empty_annotations() -> dict:
    """Return an empty THOTH-compatible annotations object."""
    return {
        "selections": {},
        "measurements": {},
        "semantic_annotations": {},
    }


def _empty_artefact(title: str = "", gltf_file: str = "") -> dict:
    """Return a THOTH-compatible artefact object."""
    return {
        "title": title,
        "gltf_file": gltf_file,
        "description": "",
        "owner": "",
        "keywords": [],
        "copyright": "",
    }


def _normalize_model(model) -> tuple[str, dict] | None:
    """Normalize one requested model into a THOTH scene model entry."""
    if isinstance(model, str):
        model_id = model.strip()
        if not model_id:
            return None

        return model_id, {
            "artefact": _empty_artefact(
                title=model_id,
                gltf_file=model_id,
            ),
            "metadata": _empty_metadata(),
            "transforms": _empty_transform(),
            "annotations": _empty_annotations(),
            "sensors": [],
        }

    if not isinstance(model, dict):
        return None

    artefact = model.get("artefact") if isinstance(model.get("artefact"), dict) else {}
    model_id = str(
        model.get("id") or
        model.get("model_id") or
        model.get("name") or
        model.get("title") or
        artefact.get("title") or
        artefact.get("gltf_file") or
        ""
    ).strip()
    if not model_id:
        return None

    gltf_file = (
        artefact.get("gltf_file") or
        model.get("gltf_file") or
        model.get("url") or
        model.get("path") or
        model.get("src") or
        ""
    )

    normalized = {
        "artefact": {
            **_empty_artefact(
                title=artefact.get("title") or model.get("title") or model_id,
                gltf_file=gltf_file,
            ),
            **artefact,
        },
        "metadata": model.get("metadata") if isinstance(model.get("metadata"), dict) else _empty_metadata(),
        "transforms": model.get("transforms") if isinstance(model.get("transforms"), dict) else _empty_transform(),
        "annotations": model.get("annotations") if isinstance(model.get("annotations"), dict) else _empty_annotations(),
        "sensors": model.get("sensors") if isinstance(model.get("sensors"), list) else [],
    }

    return model_id, normalized


def _normalize_models(models) -> dict:
    """Normalize optional model input into THOTH's canonical model map."""
    if models is None:
        return {}
    if not isinstance(models, list):
        raise ValueError("models must be a list")

    normalized_models = {}
    for model in models:
        normalized_model = _normalize_model(model)
        if normalized_model is None:
            continue

        model_id, model_data = normalized_model
        normalized_models[model_id] = model_data

    return normalized_models


def _create_scene_content(collaborative: bool = False, models=None) -> dict:
    """Create a THOTH-compatible scene content object."""
    return {
        "models": _normalize_models(models),
        "collaborative": bool(collaborative),
    }


def _read_scene(scene_id: str) -> dict | None:
    """Read one stored scene."""
    scene_path = _get_scene_path(scene_id)
    if not scene_path.exists():
        return None

    return json.loads(scene_path.read_text(encoding="utf-8"))


def _write_scene(scene_id: str, content: dict) -> None:
    """Write one stored scene."""
    scene_path = _get_scene_path(scene_id)
    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    scene_path.write_text(
        json.dumps(content, indent=4),
        encoding="utf-8",
    )


def _get_requested_scene_id(data: dict | None = None) -> str | None:
    """Read scene_id from route, query params, or JSON body."""
    data = data or {}
    return request.args.get("scene_id") or data.get("scene_id")


class SceneResource(Resource):
    method_decorators = [require_api_key]

    def post(self, scene_id: str | None = None):
        """Create an empty or model-seeded THOTH scene."""
        data = request.get_json(silent=True) or {}
        scene_id = scene_id or _get_requested_scene_id(data)
        if not scene_id:
            return {"error": "Missing scene_id"}, 400

        try:
            existing_scene = _read_scene(scene_id)
            if existing_scene is not None:
                return {
                    "message": "Scene already exists",
                    "scene_id": _clean_scene_id(scene_id),
                    "content": existing_scene,
                }, 200

            content = _create_scene_content(
                collaborative=data.get("collaborative", False),
                models=data.get("models"),
            )
            _write_scene(scene_id, content)
        except ValueError as exc:
            return {"error": str(exc)}, 400

        return {
            "message": "Scene created",
            "scene_id": _clean_scene_id(scene_id),
            "content": content,
        }, 201

    def get(self, scene_id: str | None = None):
        """Return THOTH scene content."""
        scene_id = scene_id or _get_requested_scene_id()
        if not scene_id:
            return {"error": "Missing scene_id"}, 400

        try:
            content = _read_scene(scene_id)
        except ValueError as exc:
            return {"error": str(exc)}, 400

        if content is None:
            return {"error": f"Scene '{scene_id}' not found"}, 404

        return {
            "content": content,
        }, 200

    def put(self, scene_id: str | None = None):
        """Replace THOTH scene content."""
        data = request.get_json(silent=True) or {}
        scene_id = scene_id or _get_requested_scene_id(data)
        if not scene_id:
            return {"error": "Missing scene_id"}, 400

        content = data.get("content")
        if content is None and isinstance(data.get("body"), dict):
            content = data["body"].get("content")
        if not isinstance(content, dict):
            return {"error": "Missing content object"}, 400

        if not isinstance(content.get("models", {}), dict):
            return {"error": "content.models must be an object"}, 400

        content["collaborative"] = bool(content.get("collaborative", False))
        try:
            _write_scene(scene_id, content)
        except ValueError as exc:
            return {"error": str(exc)}, 400

        return {
            "message": "Scene updated",
            "scene_id": _clean_scene_id(scene_id),
            "content": content,
        }, 200
