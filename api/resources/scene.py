import io
import json
import logging
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from flask import request
from flask_restful import Resource
from minio.error import S3Error

from middleware.security import require_api_key
from resources.artifact import ArtifactAggregateResource
from services.storage import MINIO_ARTIFACT_BUCKET, build_public_url, minio_client


logger = logging.getLogger(__name__)

SCENE_PREFIX = "scenes/"
SAFE_SCENE_ID_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
METADATA_SCHEMA_NAME = "puc_schema"

SCENE_STRUCTURE_TEMPLATE = {
    "models": {
        "<model_id>": {
            "id": "<model_id>",
            "artefact": {
                "title": "",
                "gltf_file": "",
                "description": "",
                "owner": "",
                "keywords": [],
                "copyright": "",
            },
            "metadata": {
                "schema": {
                    "name": METADATA_SCHEMA_NAME,
                    "version": "",
                    "description": "",
                    "url": "",
                },
                "attributes": {},
            },
            "transforms": {
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
            },
            "annotations": {},
            "sensors": [],
        },
    },
    "collaborative": False,
}


# ==============================================================================
# UTILS
# ==============================================================================


def _clean_scene_id(scene_id: str) -> str:
    """Return a storage-safe scene identifier."""
    clean_scene_id = SAFE_SCENE_ID_PATTERN.sub("_", str(scene_id or "").strip())
    return clean_scene_id.strip("._")


def _get_scene_object_name(scene_id: str) -> str:
    """Return the MinIO object name for a scene."""
    clean_scene_id = _clean_scene_id(scene_id)
    if not clean_scene_id:
        raise ValueError("Missing scene_id")

    return f"{SCENE_PREFIX}{clean_scene_id}.json"


def _get_scene_location(scene_id: str) -> str:
    """Return the MinIO URI for a scene."""
    object_name = _get_scene_object_name(scene_id)
    return f"s3://{MINIO_ARTIFACT_BUCKET}/{object_name}"


def _get_scene_public_url(scene_id: str) -> str:
    """Return the public API URL for a stored scene."""
    object_name = _get_scene_object_name(scene_id)
    return build_public_url(MINIO_ARTIFACT_BUCKET, object_name)


def _empty_metadata() -> dict:
    """Return an empty THOTH-compatible metadata object."""
    return {
        "schema": {
            "name": METADATA_SCHEMA_NAME,
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
    }


def _empty_annotations() -> dict:
    """Return an empty THOTH-compatible annotations object."""
    return {}


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
        gltf_file = model.strip()
        if not gltf_file:
            return None

        model_id = _get_model_id(gltf_file, {}, gltf_file)
        return model_id, {
            "id": model_id,
            "artefact": _empty_artefact(
                gltf_file=gltf_file,
            ),
            "metadata": _empty_metadata(),
            "transforms": _empty_transform(),
            "annotations": _empty_annotations(),
            "sensors": [],
        }

    if not isinstance(model, dict):
        return None

    artefact = model.get("artefact") if isinstance(model.get("artefact"), dict) else {}
    gltf_file = (
        artefact.get("gltf_file") or
        model.get("gltf_file") or
        model.get("url") or
        model.get("path") or
        model.get("src") or
        ""
    )

    model_id = str(
        model.get("id") or
        model.get("model_id") or
        model.get("name") or
        ""
    ).strip()
    if not model_id:
        model_id = _get_model_id("", artefact, gltf_file)
    if not model_id:
        return None

    normalized = {
        "id": model_id,
        "artefact": {
            **_empty_artefact(
                title=artefact.get("title") or model.get("title") or "",
                gltf_file=gltf_file,
            ),
            **artefact,
        },
        "metadata": (
            model.get("metadata")
            if isinstance(model.get("metadata"), dict)
            else _empty_metadata()
        ),
        "transforms": (
            model.get("transforms")
            if isinstance(model.get("transforms"), dict)
            else _empty_transform()
        ),
        "annotations": (
            model.get("annotations")
            if isinstance(model.get("annotations"), dict)
            else _empty_annotations()
        ),
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


def _get_resource_payload(resource_response) -> tuple[dict | None, int]:
    """Return JSON payload and HTTP status from an internal resource response."""
    status_code = 200
    response = resource_response

    if isinstance(resource_response, tuple):
        response = resource_response[0]
        if len(resource_response) > 1:
            status_code = resource_response[1]

    if hasattr(response, "get_json"):
        return response.get_json(silent=True), status_code

    return response if isinstance(response, dict) else None, status_code


def _get_artifact_payload(artifact_id: str) -> tuple[dict | None, int]:
    """Return aggregate artifact information from the existing artifact resource."""
    resource_response = ArtifactAggregateResource().get(artifact_id)
    return _get_resource_payload(resource_response)


def _get_preferred_gltf_file(artifact: dict, reconstructions: list[dict]) -> str:
    """Return the best available model URL or artifact URL for a scene artefact."""
    for reconstruction in reconstructions:
        gltf_file = (
            reconstruction.get("public_url_glb") or
            reconstruction.get("glb_location") or
            reconstruction.get("public_url_model") or
            reconstruction.get("model_location")
        )
        if gltf_file:
            return gltf_file

    return artifact.get("public_url") or artifact.get("location") or ""


def _get_model_id(artifact_id: str, artifact: dict, gltf_file: str) -> str:
    """Return the model identifier used as the scene model key."""
    if gltf_file:
        parsed_path = urlparse(gltf_file).path or gltf_file
        model_id = PurePosixPath(unquote(parsed_path)).name
        if model_id:
            return model_id

    filename = str(artifact.get("filename") or "").strip()
    return filename or artifact_id


def _create_scene_content_from_artifact(
    artifact_id: str,
    artifact_payload: dict,
    collaborative: bool = False,
) -> dict:
    """Create a scene content object around aggregate artifact information."""
    artifact = artifact_payload.get("artifact", {})
    reconstructions = artifact_payload.get("reconstructions", [])
    sensors = artifact_payload.get("sensor_readings", [])

    if not isinstance(artifact, dict):
        artifact = {}
    if not isinstance(reconstructions, list):
        reconstructions = []
    if not isinstance(sensors, list):
        sensors = []

    gltf_file = _get_preferred_gltf_file(artifact, reconstructions)
    model_id = _get_model_id(artifact_id, artifact, gltf_file)
    artefact = {
        **_empty_artefact(
            title=artifact.get("title") or "",
            gltf_file=gltf_file,
        ),
        "owner": artifact.get("uploaded_by") or "",
    }

    return {
        "models": {
            model_id: {
                "id": model_id,
                "artefact": artefact,
                "metadata": _empty_metadata(),
                "transforms": _empty_transform(),
                "annotations": {},
                "sensors": sensors,
            },
        },
        "collaborative": bool(collaborative),
    }


def _read_scene(scene_id: str) -> dict | None:
    """Read one stored scene from MinIO."""
    object_name = _get_scene_object_name(scene_id)
    response = None
    try:
        response = minio_client.get_object(MINIO_ARTIFACT_BUCKET, object_name)
        return json.loads(response.read().decode("utf-8"))
    except S3Error as exc:
        if exc.code in {"NoSuchBucket", "NoSuchKey"}:
            return None

        logger.error("Failed to read scene '%s' from MinIO: %s", scene_id, exc)
        raise
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _write_scene(scene_id: str, content: dict) -> None:
    """Write one scene to MinIO."""
    object_name = _get_scene_object_name(scene_id)
    file_data = json.dumps(content, indent=4).encode("utf-8")
    minio_client.put_object(
        MINIO_ARTIFACT_BUCKET,
        object_name,
        io.BytesIO(file_data),
        len(file_data),
        content_type="application/json",
    )


def _list_scene_ids() -> list[str]:
    """Return stored scene identifiers."""
    try:
        objects = minio_client.list_objects(
            MINIO_ARTIFACT_BUCKET,
            prefix=SCENE_PREFIX,
            recursive=True,
        )
    except S3Error as exc:
        logger.error("Failed to list scenes from MinIO: %s", exc)
        return []

    scene_ids = []
    for obj in objects:
        object_name = obj.object_name or ""
        if not object_name.endswith(".json"):
            continue

        scene_ids.append(
            object_name
            .removeprefix(SCENE_PREFIX)
            .removesuffix(".json")
        )

    return sorted(scene_ids)


def _build_scene_response(scene_id: str, content: dict) -> dict:
    """Return scene content plus MinIO references."""
    clean_scene_id = _clean_scene_id(scene_id)
    return {
        "scene_id": clean_scene_id,
        "content": content,
        "location": _get_scene_location(clean_scene_id),
        "public_url": _get_scene_public_url(clean_scene_id),
    }


def _get_requested_scene_id(data: dict | None = None) -> str | None:
    """Read scene_id from route, query params, or JSON body."""
    data = data or {}
    return request.args.get("scene_id") or data.get("scene_id")


def _get_requested_artifact_id(data: dict | None = None) -> str | None:
    """Read optional artifact_id from query params or JSON body."""
    data = data or {}
    artifact_id = request.args.get("artifact_id") or data.get("artifact_id")
    if artifact_id is None:
        return None

    artifact_id = str(artifact_id).strip()
    return artifact_id or None


# ==============================================================================
# ENDPOINT
# ==============================================================================

class SceneResource(Resource):
    method_decorators = [require_api_key]

    def post(self, scene_id: str | None = None):
        """Create an empty, model-seeded, or artifact-backed THOTH scene."""
        data = request.get_json(silent=True) or {}
        scene_id = scene_id or _get_requested_scene_id(data)
        artifact_id = _get_requested_artifact_id(data)
        if not scene_id:
            return {"error": "Missing scene_id"}, 400

        try:
            existing_scene = _read_scene(scene_id)
            if existing_scene is not None:
                response = _build_scene_response(scene_id, existing_scene)
                response["message"] = "Scene already exists"
                return response, 200

            if artifact_id is None:
                content = _create_scene_content(
                    collaborative=data.get("collaborative", False),
                    models=data.get("models"),
                )
            else:
                artifact_payload, status_code = _get_artifact_payload(artifact_id)
                if status_code >= 400:
                    return (
                        artifact_payload or {"error": "Failed to retrieve artifact"},
                        status_code,
                    )
                if not artifact_payload or not artifact_payload.get("artifact"):
                    return {"error": f"Artifact '{artifact_id}' not found"}, 404

                content = _create_scene_content_from_artifact(
                    artifact_id=artifact_id,
                    artifact_payload=artifact_payload,
                    collaborative=data.get("collaborative", False),
                )

            _write_scene(scene_id, content)
        except ValueError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:
            logger.error("Failed to create scene '%s': %s", scene_id, exc)
            return {"error": "Failed to create scene"}, 500

        response = _build_scene_response(scene_id, content)
        response["message"] = "Scene created"
        return response, 201

    def get(self, scene_id: str | None = None):
        """Return THOTH scene content."""
        scene_id = scene_id or _get_requested_scene_id()
        if not scene_id:
            return {
                "scenes": [
                    {
                        "scene_id": stored_scene_id,
                        "location": _get_scene_location(stored_scene_id),
                        "public_url": _get_scene_public_url(stored_scene_id),
                    }
                    for stored_scene_id in _list_scene_ids()
                ],
            }, 200

        try:
            content = _read_scene(scene_id)
        except ValueError as exc:
            return {"error": str(exc)}, 400
        except Exception as exc:
            logger.error("Failed to retrieve scene '%s': %s", scene_id, exc)
            return {"error": "Failed to retrieve scene"}, 500

        if content is None:
            return {"error": f"Scene '{scene_id}' not found"}, 404

        return _build_scene_response(scene_id, content), 200

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
        except Exception as exc:
            logger.error("Failed to update scene '%s': %s", scene_id, exc)
            return {"error": "Failed to update scene"}, 500

        response = _build_scene_response(scene_id, content)
        response["message"] = "Scene updated"
        return response, 200
