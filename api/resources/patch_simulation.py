from flask import request, jsonify, Response
from flask_restful import Resource
from datetime import datetime, timezone
from psycopg2.extras import Json
from minio.error import S3Error
import io
import json
import uuid
import zipfile
import logging

from middleware.security import require_api_key
from services.database import get_db_connection
from services.messaging import (
    send_avro_message,
    send_simple_message,
    TOPIC_DYNAMO_PATCH_SIMULATIONS,
    TOPIC_DYNAMO_PATCH_SIMULATION_OUTPUTS,
    TOPIC_DYNAMO_PATCH_SIMULATION_UPLOADED,
)
from services.storage import minio_client, MINIO_PATCH_SIMULATION_BUCKET
from services.simulation_visualization import build_morph_target_glb
from services.simulation_plots import render_polar_stiffness_png

logger = logging.getLogger(__name__)

INPUT_FILTERABLE_FIELDS = {'simulation_id', 'structure_type', 'weave_pattern'}

# Pairs of (JSON side key, DB column prefix) for the warp/weft sub-objects.
SIDES = (('warpInput', 'warp'), ('weftInput', 'weft'))
# {unit, value} fields inside each side. Radius→Diameter rename in the new
# schema (yarnDiameterRatio semantics also flipped: it's now major/minor axis
# ratio capped at 1, where 1 = perfect circle).
SIDE_UV_FIELDS = (
    ('youngsModulus', 'youngs_modulus'),
    ('poissonRatio', 'poisson_ratio'),
    ('yarnDiameter', 'yarn_diameter'),
    ('yarnDiameterRatio', 'yarn_diameter_ratio'),
    ('yarnCountPerDistance', 'yarn_count_per_distance'),
    ('yarnFriction', 'yarn_friction'),
)
# The 6 visualization-files arrays on the output.
VIZ_KEYS = (
    'visualizationFiles_inplane11',
    'visualizationFiles_inplane22',
    'visualizationFiles_inplane12',
    'visualizationFiles_bending11',
    'visualizationFiles_bending22',
    'visualizationFiles_bending12',
)
# Homogenized stiffness metrics + polar-plot arrays added in the new schema.
# Stored per-field as (unit TEXT, values JSONB). Value shape is always an
# array of floats; the two `effective*` arrays are 4 floats (tensor
# components), the plot arrays match plotDataAngles in length.
STIFFNESS_OUTPUT_FIELDS = (
    ('effectiveExtensionalStiffness', 'effective_extensional_stiffness'),
    ('effectiveBendingStiffness',     'effective_bending_stiffness'),
    ('plotDataAngles',                'plot_data_angles'),
    ('plotDataExtensionalStiffness',  'plot_data_extensional_stiffness'),
    ('plotDataBendingStiffness',      'plot_data_bending_stiffness'),
)


# Avro schemas are flat 1:1 with their tables. JSONB columns (the viz arrays)
# ride as Avro `string` carrying JSON; the JDBC driver casts string→JSONB.
INPUT_AVRO_SCHEMA = """
{
    "type": "record",
    "name": "PatchSimulationInput",
    "namespace": "com.textailes.dynamo",
    "fields": [
        {"name": "simulation_id", "type": "string"},
        {"name": "structure_type", "type": "string"},
        {"name": "weave_pattern", "type": ["null", "string"], "default": null},
        {"name": "pattern_repetition_count_warp", "type": ["null", "int"], "default": null},
        {"name": "pattern_repetition_count_weft", "type": ["null", "int"], "default": null},
        {"name": "warp_material", "type": ["null", "string"], "default": null},
        {"name": "warp_youngs_modulus_value", "type": ["null", "float"], "default": null},
        {"name": "warp_youngs_modulus_unit", "type": ["null", "string"], "default": null},
        {"name": "warp_poisson_ratio_value", "type": ["null", "float"], "default": null},
        {"name": "warp_poisson_ratio_unit", "type": ["null", "string"], "default": null},
        {"name": "warp_yarn_diameter_value", "type": ["null", "float"], "default": null},
        {"name": "warp_yarn_diameter_unit", "type": ["null", "string"], "default": null},
        {"name": "warp_yarn_diameter_ratio_value", "type": ["null", "float"], "default": null},
        {"name": "warp_yarn_diameter_ratio_unit", "type": ["null", "string"], "default": null},
        {"name": "warp_yarn_count_per_distance_value", "type": ["null", "float"], "default": null},
        {"name": "warp_yarn_count_per_distance_unit", "type": ["null", "string"], "default": null},
        {"name": "warp_yarn_friction_value", "type": ["null", "float"], "default": null},
        {"name": "warp_yarn_friction_unit", "type": ["null", "string"], "default": null},
        {"name": "weft_material", "type": ["null", "string"], "default": null},
        {"name": "weft_youngs_modulus_value", "type": ["null", "float"], "default": null},
        {"name": "weft_youngs_modulus_unit", "type": ["null", "string"], "default": null},
        {"name": "weft_poisson_ratio_value", "type": ["null", "float"], "default": null},
        {"name": "weft_poisson_ratio_unit", "type": ["null", "string"], "default": null},
        {"name": "weft_yarn_diameter_value", "type": ["null", "float"], "default": null},
        {"name": "weft_yarn_diameter_unit", "type": ["null", "string"], "default": null},
        {"name": "weft_yarn_diameter_ratio_value", "type": ["null", "float"], "default": null},
        {"name": "weft_yarn_diameter_ratio_unit", "type": ["null", "string"], "default": null},
        {"name": "weft_yarn_count_per_distance_value", "type": ["null", "float"], "default": null},
        {"name": "weft_yarn_count_per_distance_unit", "type": ["null", "string"], "default": null},
        {"name": "weft_yarn_friction_value", "type": ["null", "float"], "default": null},
        {"name": "weft_yarn_friction_unit", "type": ["null", "string"], "default": null},
        {"name": "discretization_intermediate_element_count", "type": ["null", "int"], "default": null},
        {"name": "created_at", "type": "string"},
        {"name": "updated_at", "type": "string"}
    ]
}
"""

OUTPUT_AVRO_SCHEMA = """
{
    "type": "record",
    "name": "PatchSimulationOutput",
    "namespace": "com.textailes.dynamo",
    "fields": [
        {"name": "simulation_id", "type": "string"},
        {"name": "simulation_completed", "type": "boolean"},
        {"name": "visualization_files_inplane11", "type": ["null", "string"], "default": null},
        {"name": "visualization_files_inplane22", "type": ["null", "string"], "default": null},
        {"name": "visualization_files_inplane12", "type": ["null", "string"], "default": null},
        {"name": "visualization_files_bending11", "type": ["null", "string"], "default": null},
        {"name": "visualization_files_bending22", "type": ["null", "string"], "default": null},
        {"name": "visualization_files_bending12", "type": ["null", "string"], "default": null},
        {"name": "effective_extensional_stiffness_unit", "type": ["null", "string"], "default": null},
        {"name": "effective_extensional_stiffness_values", "type": ["null", "string"], "default": null},
        {"name": "effective_bending_stiffness_unit", "type": ["null", "string"], "default": null},
        {"name": "effective_bending_stiffness_values", "type": ["null", "string"], "default": null},
        {"name": "plot_data_angles_unit", "type": ["null", "string"], "default": null},
        {"name": "plot_data_angles_values", "type": ["null", "string"], "default": null},
        {"name": "plot_data_extensional_stiffness_unit", "type": ["null", "string"], "default": null},
        {"name": "plot_data_extensional_stiffness_values", "type": ["null", "string"], "default": null},
        {"name": "plot_data_bending_stiffness_unit", "type": ["null", "string"], "default": null},
        {"name": "plot_data_bending_stiffness_values", "type": ["null", "string"], "default": null},
        {"name": "updated_at", "type": "string"}
    ]
}
"""

# ---------- helpers ----------

def _uv(obj, field):
    """Extract (value, unit) from a {unit, value} dict; returns (None, None) if missing."""
    sub = (obj or {}).get(field) or {}
    return sub.get('value'), sub.get('unit')


def _nest_uv(row, prefix):
    """Inverse of _uv: collapse `<prefix>_value` + `<prefix>_unit` columns into {unit, value} (or None)."""
    value = row.pop(f'{prefix}_value', None)
    unit = row.pop(f'{prefix}_unit', None)
    if value is None and unit is None:
        return None
    return {'unit': unit, 'value': value}


def _row_to_dict(cur, row):
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    out = {}
    for col, val in zip(cols, row):
        out[col] = val.isoformat() if isinstance(val, datetime) else val
    return out


def _rows_to_dicts(cur, rows):
    cols = [d[0] for d in cur.description]
    results = []
    for row in rows:
        d = {}
        for col, val in zip(cols, row):
            d[col] = val.isoformat() if isinstance(val, datetime) else val
        results.append(d)
    return results


def _side_to_columns(side_payload, db_prefix):
    """Map a warpInput/weftInput payload to flat <prefix>_* column values."""
    p = side_payload or {}
    cols = {f'{db_prefix}_material': p.get('material')}
    for json_key, db_col in SIDE_UV_FIELDS:
        value, unit = _uv(p, json_key)
        cols[f'{db_prefix}_{db_col}_value'] = value
        cols[f'{db_prefix}_{db_col}_unit'] = unit
    return cols


def _hydrate_side(row, db_prefix):
    """Inverse of _side_to_columns: build {material, youngsModulus, ...} from flat columns."""
    out = {'material': row.pop(f'{db_prefix}_material', None)}
    for json_key, db_col in SIDE_UV_FIELDS:
        out[json_key] = _nest_uv(row, f'{db_prefix}_{db_col}')
    return out


def _hydrate_record(input_row, output_row):
    """Rebuild the original JSON schema shape from the two table rows."""
    simulation_input = {
        'weavePattern': input_row.get('weave_pattern'),
        'patternRepetitionCountWarp': input_row.get('pattern_repetition_count_warp'),
        'patternRepetitionCountWeft': input_row.get('pattern_repetition_count_weft'),
        'warpInput': _hydrate_side(input_row, 'warp'),
        'weftInput': _hydrate_side(input_row, 'weft'),
        'discretization': {
            'intermediateElementCount': input_row.get('discretization_intermediate_element_count'),
        },
    }

    simulation_output = None
    if output_row:
        simulation_output = {
            'simulationCompleted': output_row.get('simulation_completed'),
            'simulationError': output_row.get('simulation_error'),
        }
        for viz_key in VIZ_KEYS:
            col = viz_key.replace('visualizationFiles_', 'visualization_files_')
            simulation_output[viz_key] = output_row.get(col) or []
        for json_key, db_prefix in STIFFNESS_OUTPUT_FIELDS:
            unit = output_row.get(f'{db_prefix}_unit')
            values = output_row.get(f'{db_prefix}_values')
            if unit is None and values is None:
                simulation_output[json_key] = None
            else:
                simulation_output[json_key] = {'unit': unit, 'value': values or []}

    return {
        'simulation_id': input_row['simulation_id'],
        'structureType': input_row.get('structure_type'),
        'created_at': input_row.get('created_at'),
        'updated_at': input_row.get('updated_at'),
        'simulationInput': simulation_input,
        'simulationOutput': simulation_output,
    }


# ---------- collection resource ----------

class PatchSimulationResource(Resource):
    method_decorators = [require_api_key]

    def get(self):
        """
        List patch simulations with their nested inputs and outputs.

        Query params:
          simulation_id, structure_type, weave_pattern — exact-match filters
          page (default 1), per_page (default 50)
        """
        conn = None
        try:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 50))
            offset = (page - 1) * per_page

            sql = "SELECT * FROM dynamo.patch_simulation_input WHERE 1=1"
            params = []
            for key in INPUT_FILTERABLE_FIELDS:
                value = request.args.get(key)
                if value:
                    sql += f" AND {key} = %s"
                    params.append(value)
            sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
            params.extend([per_page, offset])

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            input_rows = _rows_to_dicts(cur, cur.fetchall())

            if not input_rows:
                cur.close()
                conn.close()
                return jsonify([])

            sim_ids = [str(r['simulation_id']) for r in input_rows]

            cur.execute(
                "SELECT * FROM dynamo.patch_simulation_output WHERE simulation_id = ANY(%s::uuid[])",
                (sim_ids,)
            )
            output_rows = _rows_to_dicts(cur, cur.fetchall())

            cur.close()
            conn.close()

            output_by_sim = {str(o.pop('simulation_id')): o for o in output_rows}

            results = []
            for row in input_rows:
                sid = str(row['simulation_id'])
                row['simulation_id'] = sid
                results.append(_hydrate_record(row, output_by_sim.get(sid)))

            return jsonify(results)

        except Exception as e:
            logger.error(f"Error fetching patch simulations: {e}")
            if conn:
                conn.close()
            return {'error': str(e)}, 500

    def post(self):
        """
        Create a patch simulation from the portal form. Output starts empty
        (simulation_completed=false, viz arrays NULL). Simulator populates the
        output later via PATCH on /dynamo/patch-simulations/<simulation_id>.
        """
        body = request.get_json(silent=True)
        if not body:
            return {'error': "No data provided"}, 400

        sim_input = body.get('simulationInput')
        if not sim_input:
            return {'error': "Missing 'simulationInput'"}, 400

        simulation_id = body.get('simulation_id') or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        discretization = sim_input.get('discretization') or {}

        input_row = {
            'simulation_id': simulation_id,
            'structure_type': body.get('structureType', 'Patch'),
            'weave_pattern': sim_input.get('weavePattern'),
            'pattern_repetition_count_warp': sim_input.get('patternRepetitionCountWarp'),
            'pattern_repetition_count_weft': sim_input.get('patternRepetitionCountWeft'),
            'discretization_intermediate_element_count': discretization.get('intermediateElementCount'),
            'created_at': now,
            'updated_at': now,
        }
        for json_key, db_prefix in SIDES:
            input_row.update(_side_to_columns(sim_input.get(json_key), db_prefix))

        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO dynamo.patch_simulation_input ({', '.join(input_row.keys())})
                    VALUES ({', '.join(['%s'] * len(input_row))})
                    """,
                    tuple(input_row.values())
                )

                cur.execute(
                    """
                    INSERT INTO dynamo.patch_simulation_output (simulation_id, simulation_completed, updated_at)
                    VALUES (%s, FALSE, %s)
                    """,
                    (simulation_id, now)
                )

            # Publish to Kafka (input + empty output topics).
            input_event = dict(input_row)
            input_event['created_at'] = now_iso
            input_event['updated_at'] = now_iso
            if not send_avro_message(
                TOPIC_DYNAMO_PATCH_SIMULATIONS, simulation_id, input_event, INPUT_AVRO_SCHEMA
            ):
                logger.warning(f"Patch simulation {simulation_id} saved but parent Avro publish failed.")

            empty_output_event = {
                'simulation_id': simulation_id,
                'simulation_completed': False,
                'updated_at': now_iso,
            }
            for viz_key in VIZ_KEYS:
                empty_output_event[viz_key.replace('visualizationFiles_', 'visualization_files_')] = None
            for _json_key, db_prefix in STIFFNESS_OUTPUT_FIELDS:
                empty_output_event[f'{db_prefix}_unit'] = None
                empty_output_event[f'{db_prefix}_values'] = None
            if not send_avro_message(
                TOPIC_DYNAMO_PATCH_SIMULATION_OUTPUTS, simulation_id, empty_output_event, OUTPUT_AVRO_SCHEMA
            ):
                logger.warning(f"Output for {simulation_id} saved but Avro publish failed.")

            # Notification — what wakes the simulator.
            if not send_simple_message(
                TOPIC_DYNAMO_PATCH_SIMULATION_UPLOADED,
                simulation_id,
                {'status': 'submitted', 'simulation_id': simulation_id}
            ):
                logger.warning(f"Patch simulation {simulation_id} saved but Kafka notification failed.")

            return {
                'message': "Patch simulation submitted.",
                'simulation_id': simulation_id,
            }, 201

        except Exception as e:
            logger.error(f"Failed to create patch simulation: {e}")
            return {'error': str(e)}, 500


# ---------- item resource ----------

class PatchSimulationItemResource(Resource):
    method_decorators = [require_api_key]

    def get(self, simulation_id):
        """Fetch a single patch simulation, fully rehydrated."""
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM dynamo.patch_simulation_input WHERE simulation_id = %s",
                (simulation_id,)
            )
            input_row = _row_to_dict(cur, cur.fetchone())
            if not input_row:
                cur.close()
                conn.close()
                return {'error': f"Simulation '{simulation_id}' not found"}, 404

            cur.execute(
                "SELECT * FROM dynamo.patch_simulation_output WHERE simulation_id = %s",
                (simulation_id,)
            )
            output_row = _row_to_dict(cur, cur.fetchone())
            if output_row:
                output_row.pop('simulation_id', None)

            cur.close()
            conn.close()

            input_row['simulation_id'] = str(input_row['simulation_id'])
            return jsonify(_hydrate_record(input_row, output_row))

        except Exception as e:
            logger.error(f"Error fetching patch simulation {simulation_id}: {e}")
            if conn:
                conn.close()
            return {'error': str(e)}, 500

    def patch(self, simulation_id):
        """
        Update the simulation output. Called by the simulator after computation.

        Body (JSON):
          simulationOutput: {
            simulationCompleted: bool,
            visualizationFiles_inplane11: [...],
            visualizationFiles_inplane22: [...],
            visualizationFiles_inplane12: [...],
            visualizationFiles_bending11: [...],
            visualizationFiles_bending22: [...],
            visualizationFiles_bending12: [...],
            effectiveExtensionalStiffness: {unit, value:[4 floats]},
            effectiveBendingStiffness:     {unit, value:[4 floats]},
            plotDataAngles:                {unit, value:[floats]},
            plotDataExtensionalStiffness:  {unit, value:[floats]},
            plotDataBendingStiffness:      {unit, value:[floats]}
          }
        Any subset of these fields can be provided.
        """
        body = request.get_json(silent=True) or {}
        sim_output = body.get('simulationOutput')
        if not sim_output:
            return {'error': "Missing 'simulationOutput'"}, 400

        update_fields = {}
        if 'simulationCompleted' in sim_output:
            update_fields['simulation_completed'] = sim_output['simulationCompleted']
        for viz_key in VIZ_KEYS:
            if viz_key in sim_output:
                col = viz_key.replace('visualizationFiles_', 'visualization_files_')
                update_fields[col] = Json(sim_output[viz_key])
        for json_key, db_prefix in STIFFNESS_OUTPUT_FIELDS:
            if json_key in sim_output:
                stiff = sim_output[json_key] or {}
                update_fields[f'{db_prefix}_unit'] = stiff.get('unit')
                update_fields[f'{db_prefix}_values'] = Json(stiff.get('value'))
        if 'simulationError' in sim_output:
            update_fields['simulation_error'] = sim_output['simulationError']

        if not update_fields:
            return {'error': "No updatable fields provided in 'simulationOutput'"}, 400

        now = datetime.now(timezone.utc)
        update_fields['updated_at'] = now

        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                set_clause = ', '.join([f"{k} = %s" for k in update_fields.keys()])
                params = list(update_fields.values()) + [simulation_id]
                cur.execute(
                    f"UPDATE dynamo.patch_simulation_output SET {set_clause} WHERE simulation_id = %s",
                    tuple(params)
                )
                if cur.rowcount == 0:
                    return {'error': f"Simulation '{simulation_id}' not found"}, 404

                cur.execute(
                    "UPDATE dynamo.patch_simulation_input SET updated_at = %s WHERE simulation_id = %s",
                    (now, simulation_id)
                )

                cur.execute(
                    "SELECT * FROM dynamo.patch_simulation_output WHERE simulation_id = %s",
                    (simulation_id,)
                )
                refreshed = _row_to_dict(cur, cur.fetchone())

            output_event = {
                'simulation_id': simulation_id,
                'simulation_completed': bool(refreshed.get('simulation_completed')),
                'updated_at': refreshed['updated_at'],
            }
            for viz_key in VIZ_KEYS:
                col = viz_key.replace('visualizationFiles_', 'visualization_files_')
                val = refreshed.get(col)
                output_event[col] = json.dumps(val) if val is not None else None
            for _json_key, db_prefix in STIFFNESS_OUTPUT_FIELDS:
                output_event[f'{db_prefix}_unit'] = refreshed.get(f'{db_prefix}_unit')
                values = refreshed.get(f'{db_prefix}_values')
                output_event[f'{db_prefix}_values'] = json.dumps(values) if values is not None else None

            if not send_avro_message(
                TOPIC_DYNAMO_PATCH_SIMULATION_OUTPUTS, simulation_id, output_event, OUTPUT_AVRO_SCHEMA
            ):
                logger.warning(f"Output for {simulation_id} updated but Avro publish failed.")

            return {'message': 'Output updated.', 'simulation_id': simulation_id}, 200

        except Exception as e:
            logger.error(f"Failed to update patch simulation output {simulation_id}: {e}")
            return {'error': str(e)}, 500


# ---------- visualization resource ----------

# Each "experiment" is one of the 6 disjoint OBJ sets DynaMo writes for patch
# (3 in-plane × 3 bending stiffness probes). The viewer picks one at a time.
PATCH_EXPERIMENTS = {
    'inplane11': 'visualization_files_inplane11',
    'inplane22': 'visualization_files_inplane22',
    'inplane12': 'visualization_files_inplane12',
    'bending11': 'visualization_files_bending11',
    'bending22': 'visualization_files_bending22',
    'bending12': 'visualization_files_bending12',
}


def _minio_object_exists(bucket: str, key: str) -> bool:
    try:
        minio_client.stat_object(bucket, key)
        return True
    except S3Error as e:
        if e.code in ('NoSuchKey', 'NoSuchObject'):
            return False
        raise


class PatchSimulationVisualizationResource(Resource):
    """
    GET /dynamo/patch-simulations/<simulation_id>/visualization/<experiment>.glb

    `experiment` must be one of inplane11/22/12 or bending11/22/12.
    Builds a morph-target GLB from the OBJ frames of that single experiment
    and caches the result at `patch-simulations/<simulation_id>/visualization_<experiment>.glb`.
    """
    method_decorators = [require_api_key]

    def get(self, simulation_id, experiment):
        if experiment not in PATCH_EXPERIMENTS:
            return {
                'error': f"Unknown experiment '{experiment}'. Valid: {sorted(PATCH_EXPERIMENTS)}"
            }, 400

        viz_column = PATCH_EXPERIMENTS[experiment]
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                f"SELECT {viz_column}, simulation_completed "
                f"FROM dynamo.patch_simulation_output WHERE simulation_id = %s",
                (simulation_id,)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            conn = None

            if not row:
                return {'error': f"Simulation '{simulation_id}' not found"}, 404

            viz_files, completed = row
            if not completed:
                return {'error': "Simulation not completed yet."}, 409
            if not viz_files or len(viz_files) == 0:
                return {'error': f"No visualization files for experiment '{experiment}'."}, 404
            if len(viz_files) < 2:
                return {'error': "Need at least 2 OBJ frames to build an animation."}, 422

            cache_key = f"{simulation_id}/visualization_{experiment}.glb"

            if _minio_object_exists(MINIO_PATCH_SIMULATION_BUCKET, cache_key):
                logger.info(f"[patch-viz] cache HIT {cache_key}")
                obj = minio_client.get_object(MINIO_PATCH_SIMULATION_BUCKET, cache_key)
                try:
                    glb_bytes = obj.read()
                finally:
                    obj.close()
                    obj.release_conn()
                return Response(
                    glb_bytes,
                    status=200,
                    mimetype='model/gltf-binary',
                    headers={
                        'Cache-Control': 'public, max-age=3600',
                        'X-Cache': 'HIT',
                    },
                )

            logger.info(f"[patch-viz] cache MISS — building {experiment} from {len(viz_files)} OBJ frames")
            obj_texts = []
            for filename in viz_files:
                key = f"{simulation_id}/{filename}"
                logger.info(f"[patch-viz] fetching {key}")
                obj = minio_client.get_object(MINIO_PATCH_SIMULATION_BUCKET, key)
                try:
                    obj_texts.append(obj.read().decode('utf-8'))
                finally:
                    obj.close()
                    obj.release_conn()

            glb_bytes = build_morph_target_glb(obj_texts)

            minio_client.put_object(
                MINIO_PATCH_SIMULATION_BUCKET,
                cache_key,
                io.BytesIO(glb_bytes),
                length=len(glb_bytes),
                content_type='model/gltf-binary',
            )
            logger.info(f"[patch-viz] cached {cache_key} ({len(glb_bytes)} bytes)")

            return Response(
                glb_bytes,
                status=200,
                mimetype='model/gltf-binary',
                headers={
                    'Cache-Control': 'public, max-age=3600',
                    'X-Cache': 'MISS',
                },
            )

        except S3Error as e:
            logger.error(f"[patch-viz] MinIO error for {simulation_id}/{experiment}: {e}")
            return {'error': 'Storage error', 'message': str(e)}, 502
        except ValueError as e:
            logger.error(f"[patch-viz] build error for {simulation_id}/{experiment}: {e}")
            return {'error': str(e)}, 422
        except Exception as e:
            logger.exception(f"[patch-viz] failed for {simulation_id}/{experiment}: {e}")
            if conn:
                conn.close()
            return {'error': str(e)}, 500


# ---------- download resource ----------

class PatchSimulationDownloadResource(Resource):
    """
    GET /dynamo/patch-simulations/<simulation_id>/download.zip

    Returns a ZIP bundle with:
      - simulation.json          — the full sim record (input + output, hydrated)
      - extensional_stiffness.png — polar plot of plotDataAngles × plotDataExtensionalStiffness
      - bending_stiffness.png     — polar plot of plotDataAngles × plotDataBendingStiffness

    The two PNGs are skipped if their source arrays are missing/empty.
    """
    method_decorators = [require_api_key]

    def get(self, simulation_id):
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM dynamo.patch_simulation_input WHERE simulation_id = %s",
                (simulation_id,)
            )
            input_row = _row_to_dict(cur, cur.fetchone())
            if not input_row:
                cur.close()
                conn.close()
                return {'error': f"Simulation '{simulation_id}' not found"}, 404

            cur.execute(
                "SELECT * FROM dynamo.patch_simulation_output WHERE simulation_id = %s",
                (simulation_id,)
            )
            output_row = _row_to_dict(cur, cur.fetchone())
            if output_row:
                output_row.pop('simulation_id', None)

            cur.close()
            conn.close()

            input_row['simulation_id'] = str(input_row['simulation_id'])
            record = _hydrate_record(input_row, output_row)
        except Exception as e:
            logger.error(f"[patch-download] fetch failed for {simulation_id}: {e}")
            if conn:
                conn.close()
            return {'error': str(e)}, 500

        sim_output = record.get('simulationOutput') or {}
        angles_obj = sim_output.get('plotDataAngles') or {}
        ext_obj = sim_output.get('plotDataExtensionalStiffness') or {}
        bend_obj = sim_output.get('plotDataBendingStiffness') or {}
        angles_values = angles_obj.get('value')
        angle_unit = angles_obj.get('unit') or 'deg'

        ext_png = render_polar_stiffness_png(
            angles_values,
            ext_obj.get('value'),
            angle_unit=angle_unit,
            value_unit=ext_obj.get('unit') or '',
            title='Effective Extensional Stiffness',
            color='#b8bf1a',
        )
        bend_png = render_polar_stiffness_png(
            angles_values,
            bend_obj.get('value'),
            angle_unit=angle_unit,
            value_unit=bend_obj.get('unit') or '',
            title='Effective Bending Stiffness',
            color='#17becf',
        )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('simulation.json', json.dumps(record, indent=2, default=str))
            if ext_png:
                zf.writestr('extensional_stiffness.png', ext_png)
            if bend_png:
                zf.writestr('bending_stiffness.png', bend_png)

        return Response(
            buf.getvalue(),
            status=200,
            mimetype='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename="patch-simulation-{simulation_id}.zip"',
                'Cache-Control': 'no-store',
            },
        )