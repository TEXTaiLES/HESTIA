from flask import request, jsonify
from flask_restful import Resource
from datetime import datetime, timezone
from psycopg2.extras import Json
import json
import uuid
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

logger = logging.getLogger(__name__)

INPUT_FILTERABLE_FIELDS = {'simulation_id', 'structure_type', 'weave_pattern'}

# Pairs of (JSON side key, DB column prefix) for the warp/weft sub-objects.
SIDES = (('warpInput', 'warp'), ('weftInput', 'weft'))
# {unit, value} fields inside each side.
SIDE_UV_FIELDS = (
    ('youngsModulus', 'youngs_modulus'),
    ('poissonRatio', 'poisson_ratio'),
    ('yarnRadius', 'yarn_radius'),
    ('yarnRadiusRatio', 'yarn_radius_ratio'),
    ('yarnCountPerDistance', 'yarn_count_per_distance'),
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
        {"name": "warp_yarn_radius_value", "type": ["null", "float"], "default": null},
        {"name": "warp_yarn_radius_unit", "type": ["null", "string"], "default": null},
        {"name": "warp_yarn_radius_ratio_value", "type": ["null", "float"], "default": null},
        {"name": "warp_yarn_radius_ratio_unit", "type": ["null", "string"], "default": null},
        {"name": "warp_yarn_count_per_distance_value", "type": ["null", "float"], "default": null},
        {"name": "warp_yarn_count_per_distance_unit", "type": ["null", "string"], "default": null},
        {"name": "weft_material", "type": ["null", "string"], "default": null},
        {"name": "weft_youngs_modulus_value", "type": ["null", "float"], "default": null},
        {"name": "weft_youngs_modulus_unit", "type": ["null", "string"], "default": null},
        {"name": "weft_poisson_ratio_value", "type": ["null", "float"], "default": null},
        {"name": "weft_poisson_ratio_unit", "type": ["null", "string"], "default": null},
        {"name": "weft_yarn_radius_value", "type": ["null", "float"], "default": null},
        {"name": "weft_yarn_radius_unit", "type": ["null", "string"], "default": null},
        {"name": "weft_yarn_radius_ratio_value", "type": ["null", "float"], "default": null},
        {"name": "weft_yarn_radius_ratio_unit", "type": ["null", "string"], "default": null},
        {"name": "weft_yarn_count_per_distance_value", "type": ["null", "float"], "default": null},
        {"name": "weft_yarn_count_per_distance_unit", "type": ["null", "string"], "default": null},
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
        simulation_output = {'simulationCompleted': output_row.get('simulation_completed')}
        for viz_key in VIZ_KEYS:
            col = viz_key.replace('visualizationFiles_', 'visualization_files_')
            simulation_output[viz_key] = output_row.get(col) or []

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
            visualizationFiles_bending12: [...]
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

            if not send_avro_message(
                TOPIC_DYNAMO_PATCH_SIMULATION_OUTPUTS, simulation_id, output_event, OUTPUT_AVRO_SCHEMA
            ):
                logger.warning(f"Output for {simulation_id} updated but Avro publish failed.")

            return {'message': 'Output updated.', 'simulation_id': simulation_id}, 200

        except Exception as e:
            logger.error(f"Failed to update patch simulation output {simulation_id}: {e}")
            return {'error': str(e)}, 500