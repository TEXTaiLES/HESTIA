import json
import logging
from copy import deepcopy

from flask import request
from flask_restful import Resource

from middleware.security import require_api_key
from resources.echoes import enrich_digital_twin, register_digital_twin
from services.database import get_db_connection
from services.directus import (
    ensure_json_fields,
    get_artefact_digital_twin_uri,
    get_artefact_item,
    get_asset_url,
    set_artefact_digital_twin_uri,
    update_artefact_item,
)
from scripts.convert_schema import (
    TEXTAILES_JSON_TEMPLATE,
    textailes_json_to_xml,
    textailes_to_hdto,
    textailes_xml_to_json,
)


logger = logging.getLogger(__name__)

SCHEMA_TEXTAILES = "textailes"
SCHEMA_HDTO = "hdto"

FORMAT_JSON = "json"
FORMAT_XML = "xml"

# Directus JSON fields on the artefacts collection that hold THOTH-exported
# metadata. Created on demand for deployments that predate them.
ARTEFACT_JSON_FIELDS = ("ch_metadata", "annotations")

MODEL_FILE_EXTENSIONS = (".glb", ".gltf")


# ==============================================================================
# FULL METADATA ASSEMBLY
# ==============================================================================


def _get_sensor_data(artefact_id: str) -> list[dict]:
    """Return sensor readings linked to the artefact (empty if table absent)."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.sensor_readings')")
        if cur.fetchone()[0] is None:
            return []

        cur.execute(
            "SELECT * FROM sensor_readings WHERE artifact_id = %s",
            (str(artefact_id),),
        )
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(colnames, row)) for row in rows]
    except Exception as exc:
        logger.warning("Sensor data lookup failed for artefact %s: %s", artefact_id, exc)
        return []
    finally:
        if conn:
            conn.close()


def _get_artefact_files(item: dict) -> list[dict]:
    """Return the artefact's Directus files as name/type/url entries."""
    files = []
    for junction in item.get("gltf_file") or []:
        if not isinstance(junction, dict):
            continue
        file_info = junction.get("directus_files_id")
        if isinstance(file_info, dict):
            file_id = file_info.get("id")
            filename = file_info.get("filename_download") or file_info.get("title") or ""
            file_type = file_info.get("type") or ""
        else:
            file_id = file_info
            filename = ""
            file_type = ""
        if not file_id:
            continue

        files.append({
            "id": file_id,
            "filename": filename,
            "type": file_type,
            "url": get_asset_url(file_id),
        })

    return files


def _get_preferred_model_url(files: list[dict]) -> str:
    """Return the URL of the artefact's 3D model file (or first file)."""
    for entry in files:
        if str(entry.get("filename", "")).lower().endswith(MODEL_FILE_EXTENSIONS):
            return entry["url"]
    for entry in files:
        if "model" in str(entry.get("type", "")).lower():
            return entry["url"]

    return files[0]["url"] if files else ""


def build_artefact_full_metadata(artefact_id: str) -> dict | None:
    """Assemble the artefact_full_metadata structure for a Directus artefact.

    Structure:
        artefact_full_metadata
        |-- artefact_details
        |-- annotations
        |-- ch_metadata
        |-- sensor_data
    """
    item = get_artefact_item(artefact_id)
    if item is None:
        return None

    files = _get_artefact_files(item)
    artefact_details = {
        key: value
        for key, value in item.items()
        if key not in ("gltf_file", *ARTEFACT_JSON_FIELDS)
    }
    artefact_details["files"] = files
    artefact_details["gltf_file"] = _get_preferred_model_url(files)

    ch_metadata = item.get("ch_metadata")
    if not isinstance(ch_metadata, dict) or not ch_metadata:
        ch_metadata = deepcopy(TEXTAILES_JSON_TEMPLATE)

    annotations = item.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {}

    return {
        "artefact_details": artefact_details,
        "annotations": annotations,
        "ch_metadata": ch_metadata,
        "sensor_data": _get_sensor_data(artefact_id),
    }


# ==============================================================================
# SHAPE CONVERSIONS (full metadata <-> convert_schema artefact shape)
# ==============================================================================


def _to_converter_shape(artefact_id: str, full_metadata: dict) -> dict:
    """Map artefact_full_metadata to the shape convert_schema expects."""
    details = full_metadata.get("artefact_details") or {}
    ch_metadata = full_metadata.get("ch_metadata") or {}

    title = (
        ch_metadata.get("documentation", {})
        .get("identification", {})
        .get("reference_name")
        or details.get("use_case")
        or str(artefact_id)
    )

    sensors = {}
    for reading in full_metadata.get("sensor_data") or []:
        sensor_id = str(reading.get("sensor_id", ""))
        if sensor_id and sensor_id not in sensors:
            sensors[sensor_id] = {"name": sensor_id, "id": sensor_id, "url": ""}

    return {
        str(artefact_id): {
            "artefact": {
                "title": title,
                "gltf_file": details.get("gltf_file", ""),
                "description": "",
                "owner": "",
                "keywords": [],
                "copyright": "",
            },
            "metadata": ch_metadata,
            "annotations": full_metadata.get("annotations") or {},
            "sensors": sensors,
        },
    }


def _from_converter_shape(converter_json: dict) -> dict:
    """Map a convert_schema artefact object back to artefact_full_metadata."""
    if not converter_json:
        raise ValueError("artefact_metadata must contain at least one artefact")

    _, artefact_data = next(iter(converter_json.items()))
    if not isinstance(artefact_data, dict):
        raise ValueError("artefact metadata must be a JSON object")

    return {
        "artefact_details": artefact_data.get("artefact") or {},
        "annotations": artefact_data.get("annotations") or {},
        "ch_metadata": artefact_data.get("metadata") or {},
        "sensor_data": artefact_data.get("sensors") or [],
    }


def _normalize_full_metadata(metadata) -> dict:
    """Accept the full-metadata, THOTH scene-model, or converter JSON shapes."""
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    if not isinstance(metadata, dict):
        raise ValueError("artefact_metadata must be a JSON object")

    # "annotations" exists in both shapes, so detect on unambiguous keys only.
    full_keys = {"artefact_details", "ch_metadata", "sensor_data"}
    if full_keys & set(metadata.keys()):
        return metadata

    # THOTH scene model entry: {artefact, metadata, annotations, sensors}
    if "artefact" in metadata or "metadata" in metadata:
        return _from_converter_shape({"_": metadata})

    # Converter shape: {<artefact_key>: {artefact, metadata, ...}}
    first_value = next(iter(metadata.values()), None)
    if isinstance(first_value, dict) and ("artefact" in first_value or "metadata" in first_value):
        return _from_converter_shape(metadata)

    raise ValueError("Unrecognized artefact_metadata structure")


# ==============================================================================
# ENDPOINT
# ==============================================================================


class ArtefactMetadataResource(Resource):
    """GET/PUT /artefact_metadata — the demo's metadata exchange endpoint.

    HESTIA stores artefacts with the TEXTaiLES schema; requesting or pushing
    schema=hdto triggers conversion and the export to ECHOES.
    """

    method_decorators = [require_api_key]

    def get(self, artefact_id: str | None = None):
        """Return artefact metadata in the requested schema and format."""
        artefact_id = artefact_id or request.args.get("artefact_id")
        if not artefact_id:
            return {"error": "Missing artefact_id"}, 400

        schema = (request.args.get("schema") or SCHEMA_TEXTAILES).lower()
        output_format = (request.args.get("format") or FORMAT_JSON).lower()
        if schema not in (SCHEMA_TEXTAILES, SCHEMA_HDTO):
            return {"error": f"Unsupported schema '{schema}'"}, 400
        if output_format not in (FORMAT_JSON, FORMAT_XML):
            return {"error": f"Unsupported format '{output_format}'"}, 400

        full_metadata = build_artefact_full_metadata(artefact_id)
        if full_metadata is None:
            return {"error": f"Artefact '{artefact_id}' not found"}, 404

        try:
            if schema == SCHEMA_TEXTAILES:
                if output_format == FORMAT_JSON:
                    artefact_metadata = full_metadata
                else:
                    artefact_metadata = textailes_json_to_xml(
                        _to_converter_shape(artefact_id, full_metadata)
                    )
            else:
                textailes_xml = textailes_json_to_xml(
                    _to_converter_shape(artefact_id, full_metadata)
                )
                artefact_metadata = textailes_to_hdto(textailes_xml)
                output_format = FORMAT_XML
        except Exception as exc:
            logger.error("Metadata conversion failed for artefact %s: %s", artefact_id, exc)
            return {"error": f"Metadata conversion failed: {exc}"}, 500

        return {
            "artefact_id": artefact_id,
            "schema": schema,
            "format": output_format,
            "artefact_metadata": artefact_metadata,
        }, 200

    def put(self, artefact_id: str | None = None):
        """Persist TEXTaiLES metadata to HESTIA, or export it to ECHOES as HDTO."""
        data = request.get_json(silent=True) or {}
        artefact_id = artefact_id or data.get("artefact_id")
        if not artefact_id:
            return {"error": "Missing artefact_id"}, 400

        schema = (data.get("schema") or SCHEMA_TEXTAILES).lower()
        input_format = (data.get("format") or FORMAT_JSON).lower()
        metadata = data.get("artefact_metadata")

        if schema == SCHEMA_TEXTAILES:
            return self._export_to_hestia(artefact_id, metadata, input_format)
        if schema == SCHEMA_HDTO:
            return self._export_to_echoes(artefact_id, metadata, input_format)

        return {"error": f"Unsupported schema '{schema}'"}, 400

    def _export_to_hestia(self, artefact_id: str, metadata, input_format: str):
        """Store THOTH-exported metadata on the Directus artefact."""
        if metadata is None:
            return {"error": "Missing artefact_metadata"}, 400

        try:
            if input_format == FORMAT_XML:
                full_metadata = _from_converter_shape(textailes_xml_to_json(metadata))
            else:
                full_metadata = _normalize_full_metadata(metadata)
        except Exception as exc:
            return {"error": f"Invalid artefact_metadata: {exc}"}, 400

        payload = {}
        if isinstance(full_metadata.get("ch_metadata"), dict):
            payload["ch_metadata"] = full_metadata["ch_metadata"]
        if isinstance(full_metadata.get("annotations"), dict):
            payload["annotations"] = full_metadata["annotations"]
        if not payload:
            return {"error": "artefact_metadata contains no ch_metadata or annotations"}, 400

        if get_artefact_item(artefact_id, fields="id") is None:
            return {"error": f"Artefact '{artefact_id}' not found"}, 404

        ensure_json_fields(ARTEFACT_JSON_FIELDS)
        if not update_artefact_item(artefact_id, payload):
            return {"error": f"Failed to update artefact '{artefact_id}'"}, 502

        return {
            "message": "Artefact metadata exported to HESTIA",
            "artefact_id": artefact_id,
            "updated_fields": sorted(payload.keys()),
        }, 200

    def _export_to_echoes(self, artefact_id: str, metadata, input_format: str):
        """Convert TEXTaiLES metadata to HDTO RDF and push it to ECHOES."""
        try:
            if metadata is None:
                full_metadata = build_artefact_full_metadata(artefact_id)
                if full_metadata is None:
                    return {"error": f"Artefact '{artefact_id}' not found"}, 404
                textailes_xml = textailes_json_to_xml(
                    _to_converter_shape(artefact_id, full_metadata)
                )
            elif input_format == FORMAT_XML:
                textailes_xml = metadata
            else:
                full_metadata = _normalize_full_metadata(metadata)
                textailes_xml = textailes_json_to_xml(
                    _to_converter_shape(artefact_id, full_metadata)
                )

            hdto_rdf = textailes_to_hdto(textailes_xml)
        except Exception as exc:
            logger.error("HDTO conversion failed for artefact %s: %s", artefact_id, exc)
            return {"error": f"HDTO conversion failed: {exc}"}, 500

        dt_uri = get_artefact_digital_twin_uri(artefact_id)
        registration = None
        if not dt_uri:
            dt_uri, registration = register_digital_twin(artefact_id)
            if not dt_uri:
                return {
                    "error": "ECHOES registration failed or did not include dtUri",
                    "response": registration,
                }, 502
            set_artefact_digital_twin_uri(artefact_id, dt_uri)

        success, echoes_response = enrich_digital_twin(
            dt_uri,
            hdto_rdf.encode("utf-8"),
            filename=f"{artefact_id}.rdf",
        )
        if not success:
            return {
                "error": "ECHOES enrichment failed",
                "response": echoes_response,
            }, 502

        return {
            "message": "Artefact metadata exported to ECHOES",
            "artefact_id": artefact_id,
            "dtUri": dt_uri,
            "echoes_response": echoes_response,
        }, 200
