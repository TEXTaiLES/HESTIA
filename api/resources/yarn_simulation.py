from flask import request, jsonify, Response
from flask_restful import Resource
from datetime import datetime, timezone
from psycopg2.extras import Json
from minio.error import S3Error
import io
import json
import re
import uuid
import logging

from middleware.security import require_api_key
from services.database import get_db_connection
from services.messaging import (
    send_avro_message,
    send_simple_message,
    TOPIC_DYNAMO_YARN_SIMULATIONS,
    TOPIC_DYNAMO_YARN_SIMULATION_LEVELS,
    TOPIC_DYNAMO_YARN_SIMULATION_OUTPUTS,
    TOPIC_DYNAMO_YARN_SIMULATION_UPLOADED,
)
from services.storage import minio_client, MINIO_YARN_SIMULATION_BUCKET
from services.yarn_visualization import build_morph_target_glb

logger = logging.getLogger(__name__)

INPUT_FILTERABLE_FIELDS = {'simulation_id', 'structure_type'}


# Each Avro schema is flat and 1:1 with its Postgres table. JSONB columns (the output
# arrays) ride as Avro `string` carrying JSON; the JDBC driver casts string→JSONB.
INPUT_AVRO_SCHEMA = """
{
    "type": "record",
    "name": "YarnSimulationInput",
    "namespace": "com.textailes.dynamo",
    "fields": [
        {"name": "simulation_id", "type": "string"},
        {"name": "structure_type", "type": "string"},
        {"name": "yarn_level_count", "type": "int"},
        {"name": "yarn_friction_value", "type": ["null", "float"], "default": null},
        {"name": "yarn_friction_unit", "type": ["null", "string"], "default": null},
        {"name": "yarn_adhesion_value", "type": ["null", "float"], "default": null},
        {"name": "yarn_adhesion_unit", "type": ["null", "string"], "default": null},
        {"name": "discretization_period_count", "type": ["null", "int"], "default": null},
        {"name": "discretization_nodes_per_period_count", "type": ["null", "int"], "default": null},
        {"name": "applied_elongation_value", "type": ["null", "float"], "default": null},
        {"name": "applied_elongation_unit", "type": ["null", "string"], "default": null},
        {"name": "created_at", "type": "string"},
        {"name": "updated_at", "type": "string"}
    ]
}
"""

LEVEL_AVRO_SCHEMA = """
{
    "type": "record",
    "name": "YarnSimulationInputLevel",
    "namespace": "com.textailes.dynamo",
    "fields": [
        {"name": "level_id", "type": "string"},
        {"name": "simulation_id", "type": "string"},
        {"name": "level_number", "type": "int"},
        {"name": "substructure_count", "type": ["null", "int"], "default": null},
        {"name": "twist_core", "type": ["null", "string"], "default": null},
        {"name": "pitch_core_value", "type": ["null", "float"], "default": null},
        {"name": "pitch_core_unit", "type": ["null", "string"], "default": null},
        {"name": "material_core", "type": ["null", "string"], "default": null},
        {"name": "youngs_modulus_core_value", "type": ["null", "float"], "default": null},
        {"name": "youngs_modulus_core_unit", "type": ["null", "string"], "default": null},
        {"name": "poisson_ratio_core_value", "type": ["null", "float"], "default": null},
        {"name": "poisson_ratio_core_unit", "type": ["null", "string"], "default": null},
        {"name": "radius_core_value", "type": ["null", "float"], "default": null},
        {"name": "radius_core_unit", "type": ["null", "string"], "default": null},
        {"name": "outer_strand_count", "type": ["null", "int"], "default": null},
        {"name": "twist_outer", "type": ["null", "string"], "default": null},
        {"name": "pitch_outer_value", "type": ["null", "float"], "default": null},
        {"name": "pitch_outer_unit", "type": ["null", "string"], "default": null},
        {"name": "material_outer", "type": ["null", "string"], "default": null},
        {"name": "youngs_modulus_outer_value", "type": ["null", "float"], "default": null},
        {"name": "youngs_modulus_outer_unit", "type": ["null", "string"], "default": null},
        {"name": "poisson_ratio_outer_value", "type": ["null", "float"], "default": null},
        {"name": "poisson_ratio_outer_unit", "type": ["null", "string"], "default": null},
        {"name": "radius_outer_value", "type": ["null", "float"], "default": null},
        {"name": "radius_outer_unit", "type": ["null", "string"], "default": null}
    ]
}
"""

OUTPUT_AVRO_SCHEMA = """
{
    "type": "record",
    "name": "YarnSimulationOutput",
    "namespace": "com.textailes.dynamo",
    "fields": [
        {"name": "simulation_id", "type": "string"},
        {"name": "simulation_completed", "type": "boolean"},
        {"name": "elongations_unit", "type": ["null", "string"], "default": null},
        {"name": "elongations_values", "type": ["null", "string"], "default": null},
        {"name": "forces_unit", "type": ["null", "string"], "default": null},
        {"name": "forces_values", "type": ["null", "string"], "default": null},
        {"name": "visualization_files", "type": ["null", "string"], "default": null},
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


def _extract_levels(sim_input):
    """Find inputLevel1, inputLevel2, ... keys in any order. Returns list of (level_number, payload)."""
    out = []
    for key, payload in (sim_input or {}).items():
        m = re.match(r'^inputLevel(\d+)$', key)
        if m:
            out.append((int(m.group(1)), payload))
    out.sort(key=lambda t: t[0])
    return out


def _level_to_columns(level_payload, simulation_id, level_id, level_number):
    """Map a single inputLevelN payload to flat column values for INSERT."""
    p = level_payload or {}
    pitch_core_v, pitch_core_u = _uv(p, 'pitchCore')
    ym_core_v, ym_core_u = _uv(p, 'youngsModulusCore')
    pr_core_v, pr_core_u = _uv(p, 'poissonRatioCore')
    rad_core_v, rad_core_u = _uv(p, 'radiusCore')
    pitch_outer_v, pitch_outer_u = _uv(p, 'pitchOuter')
    ym_outer_v, ym_outer_u = _uv(p, 'youngsModulusOuter')
    pr_outer_v, pr_outer_u = _uv(p, 'poissonRatioOuter')
    rad_outer_v, rad_outer_u = _uv(p, 'radiusOuter')
    return {
        'level_id': level_id,
        'simulation_id': simulation_id,
        'level_number': level_number,
        'substructure_count': p.get('substructureCount'),
        'twist_core': p.get('twistCore'),
        'pitch_core_value': pitch_core_v,
        'pitch_core_unit': pitch_core_u,
        'material_core': p.get('materialCore'),
        'youngs_modulus_core_value': ym_core_v,
        'youngs_modulus_core_unit': ym_core_u,
        'poisson_ratio_core_value': pr_core_v,
        'poisson_ratio_core_unit': pr_core_u,
        'radius_core_value': rad_core_v,
        'radius_core_unit': rad_core_u,
        'outer_strand_count': p.get('outerStrandCount'),
        'twist_outer': p.get('twistOuter'),
        'pitch_outer_value': pitch_outer_v,
        'pitch_outer_unit': pitch_outer_u,
        'material_outer': p.get('materialOuter'),
        'youngs_modulus_outer_value': ym_outer_v,
        'youngs_modulus_outer_unit': ym_outer_u,
        'poisson_ratio_outer_value': pr_outer_v,
        'poisson_ratio_outer_unit': pr_outer_u,
        'radius_outer_value': rad_outer_v,
        'radius_outer_unit': rad_outer_u,
    }


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


def _hydrate_level(level_row):
    """Inverse of _level_to_columns: turn flat row back into the nested JSON shape."""
    return {
        'substructureCount': level_row.pop('substructure_count', None),
        'twistCore': level_row.pop('twist_core', None),
        'pitchCore': _nest_uv(level_row, 'pitch_core'),
        'materialCore': level_row.pop('material_core', None),
        'youngsModulusCore': _nest_uv(level_row, 'youngs_modulus_core'),
        'poissonRatioCore': _nest_uv(level_row, 'poisson_ratio_core'),
        'radiusCore': _nest_uv(level_row, 'radius_core'),
        'outerStrandCount': level_row.pop('outer_strand_count', None),
        'twistOuter': level_row.pop('twist_outer', None),
        'pitchOuter': _nest_uv(level_row, 'pitch_outer'),
        'materialOuter': level_row.pop('material_outer', None),
        'youngsModulusOuter': _nest_uv(level_row, 'youngs_modulus_outer'),
        'poissonRatioOuter': _nest_uv(level_row, 'poisson_ratio_outer'),
        'radiusOuter': _nest_uv(level_row, 'radius_outer'),
    }


def _hydrate_record(input_row, level_rows, output_row):
    """Rebuild the original JSON schema shape from the three table rows."""
    simulation_input = {
        'yarnLevelCount': input_row.get('yarn_level_count'),
        'yarnFriction': _nest_uv(input_row, 'yarn_friction'),
        'yarnAdhesion': _nest_uv(input_row, 'yarn_adhesion'),
        'discretization': {
            'periodCount': input_row.get('discretization_period_count'),
            'nodesPerPeriodCount': input_row.get('discretization_nodes_per_period_count'),
        },
        'appliedElongation': _nest_uv(input_row, 'applied_elongation'),
    }
    for lvl in sorted(level_rows, key=lambda r: r['level_number']):
        simulation_input[f'inputLevel{lvl["level_number"]}'] = _hydrate_level(lvl)

    simulation_output = None
    if output_row:
        simulation_output = {
            'simulationCompleted': output_row.get('simulation_completed'),
            'elongations': {
                'unit': output_row.get('elongations_unit'),
                'value': output_row.get('elongations_values') or [],
            },
            'forces': {
                'unit': output_row.get('forces_unit'),
                'value': output_row.get('forces_values') or [],
            },
            'visualizationFiles': output_row.get('visualization_files') or [],
            'simulationError': output_row.get('simulation_error'),
        }

    return {
        'simulation_id': input_row['simulation_id'],
        'structureType': input_row.get('structure_type'),
        'created_at': input_row.get('created_at'),
        'updated_at': input_row.get('updated_at'),
        'simulationInput': simulation_input,
        'simulationOutput': simulation_output,
    }


# ---------- collection resource ----------

class YarnSimulationResource(Resource):
    method_decorators = [require_api_key]

    def get(self):
        """
        List yarn simulations with their nested inputs and outputs.

        Query params:
          simulation_id, structure_type — exact-match filters
          page (default 1), per_page (default 50)
        """
        conn = None
        try:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 50))
            offset = (page - 1) * per_page

            sql = "SELECT * FROM dynamo.yarn_simulation_input WHERE 1=1"
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
                "SELECT * FROM dynamo.yarn_simulation_input_level WHERE simulation_id = ANY(%s::uuid[]) ORDER BY level_number ASC",
                (sim_ids,)
            )
            level_rows = _rows_to_dicts(cur, cur.fetchall())

            cur.execute(
                "SELECT * FROM dynamo.yarn_simulation_output WHERE simulation_id = ANY(%s::uuid[])",
                (sim_ids,)
            )
            output_rows = _rows_to_dicts(cur, cur.fetchall())

            cur.close()
            conn.close()

            levels_by_sim = {sid: [] for sid in sim_ids}
            for lvl in level_rows:
                sid = str(lvl.pop('simulation_id'))
                lvl.pop('level_id', None)
                levels_by_sim.setdefault(sid, []).append(lvl)

            output_by_sim = {str(o.pop('simulation_id')): o for o in output_rows}

            results = []
            for row in input_rows:
                sid = str(row['simulation_id'])
                row['simulation_id'] = sid
                results.append(_hydrate_record(row, levels_by_sim.get(sid, []), output_by_sim.get(sid)))

            return jsonify(results)

        except Exception as e:
            logger.error(f"Error fetching yarn simulations: {e}")
            if conn:
                conn.close()
            return {'error': str(e)}, 500

    def post(self):
        """
        Create a yarn simulation from the portal form. Output starts empty
        (simulation_completed=false, arrays NULL). The simulator populates
        the output later via PATCH on /dynamo/yarn-simulations/<simulation_id>.
        """
        body = request.get_json(silent=True)
        if not body:
            return {'error': "No data provided"}, 400

        sim_input = body.get('simulationInput')
        if not sim_input:
            return {'error': "Missing 'simulationInput'"}, 400

        yarn_level_count = sim_input.get('yarnLevelCount')
        if yarn_level_count is None:
            return {'error': "Missing 'simulationInput.yarnLevelCount'"}, 400

        levels = _extract_levels(sim_input)
        if not levels:
            return {'error': "No 'inputLevelN' entries found in simulationInput"}, 400
        if len(levels) != yarn_level_count:
            return {
                'error': f"yarnLevelCount={yarn_level_count} but found {len(levels)} inputLevel entries"
            }, 400

        simulation_id = body.get('simulation_id') or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        yarn_friction_v, yarn_friction_u = _uv(sim_input, 'yarnFriction')
        yarn_adhesion_v, yarn_adhesion_u = _uv(sim_input, 'yarnAdhesion')
        applied_elongation_v, applied_elongation_u = _uv(sim_input, 'appliedElongation')
        discretization = sim_input.get('discretization') or {}

        input_row = {
            'simulation_id': simulation_id,
            'structure_type': body.get('structureType', 'Yarn'),
            'yarn_level_count': yarn_level_count,
            'yarn_friction_value': yarn_friction_v,
            'yarn_friction_unit': yarn_friction_u,
            'yarn_adhesion_value': yarn_adhesion_v,
            'yarn_adhesion_unit': yarn_adhesion_u,
            'discretization_period_count': discretization.get('periodCount'),
            'discretization_nodes_per_period_count': discretization.get('nodesPerPeriodCount'),
            'applied_elongation_value': applied_elongation_v,
            'applied_elongation_unit': applied_elongation_u,
            'created_at': now,
            'updated_at': now,
        }

        level_rows = []
        for level_number, level_payload in levels:
            level_id = str(uuid.uuid4())
            level_rows.append(_level_to_columns(level_payload, simulation_id, level_id, level_number))

        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO dynamo.yarn_simulation_input ({', '.join(input_row.keys())})
                    VALUES ({', '.join(['%s'] * len(input_row))})
                    """,
                    tuple(input_row.values())
                )

                for lvl in level_rows:
                    cur.execute(
                        f"""
                        INSERT INTO dynamo.yarn_simulation_input_level ({', '.join(lvl.keys())})
                        VALUES ({', '.join(['%s'] * len(lvl))})
                        """,
                        tuple(lvl.values())
                    )

                cur.execute(
                    """
                    INSERT INTO dynamo.yarn_simulation_output (simulation_id, simulation_completed, updated_at)
                    VALUES (%s, FALSE, %s)
                    """,
                    (simulation_id, now)
                )

            # Publish Avro to Kafka (3 topics, mirrors restoration's pattern).
            input_event = dict(input_row)
            input_event['created_at'] = now_iso
            input_event['updated_at'] = now_iso
            if not send_avro_message(
                TOPIC_DYNAMO_YARN_SIMULATIONS, simulation_id, input_event, INPUT_AVRO_SCHEMA
            ):
                logger.warning(f"Yarn simulation {simulation_id} saved but parent Avro publish failed.")

            for lvl in level_rows:
                if not send_avro_message(
                    TOPIC_DYNAMO_YARN_SIMULATION_LEVELS, lvl['level_id'], lvl, LEVEL_AVRO_SCHEMA
                ):
                    logger.warning(f"Level {lvl['level_id']} saved but Avro publish failed.")

            empty_output_event = {
                'simulation_id': simulation_id,
                'simulation_completed': False,
                'elongations_unit': None,
                'elongations_values': None,
                'forces_unit': None,
                'forces_values': None,
                'visualization_files': None,
                'updated_at': now_iso,
            }
            if not send_avro_message(
                TOPIC_DYNAMO_YARN_SIMULATION_OUTPUTS, simulation_id, empty_output_event, OUTPUT_AVRO_SCHEMA
            ):
                logger.warning(f"Output for {simulation_id} saved but Avro publish failed.")

            # Notification — what wakes the simulator.
            if not send_simple_message(
                TOPIC_DYNAMO_YARN_SIMULATION_UPLOADED,
                simulation_id,
                {'status': 'submitted', 'simulation_id': simulation_id}
            ):
                logger.warning(f"Yarn simulation {simulation_id} saved but Kafka notification failed.")

            return {
                'message': "Yarn simulation submitted.",
                'simulation_id': simulation_id,
                'level_ids': [lvl['level_id'] for lvl in level_rows],
            }, 201

        except Exception as e:
            logger.error(f"Failed to create yarn simulation: {e}")
            return {'error': str(e)}, 500


# ---------- item resource ----------

class YarnSimulationItemResource(Resource):
    method_decorators = [require_api_key]

    def get(self, simulation_id):
        """Fetch a single yarn simulation, fully rehydrated."""
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM dynamo.yarn_simulation_input WHERE simulation_id = %s",
                (simulation_id,)
            )
            input_row = _row_to_dict(cur, cur.fetchone())
            if not input_row:
                cur.close()
                conn.close()
                return {'error': f"Simulation '{simulation_id}' not found"}, 404

            cur.execute(
                "SELECT * FROM dynamo.yarn_simulation_input_level WHERE simulation_id = %s ORDER BY level_number ASC",
                (simulation_id,)
            )
            level_rows = _rows_to_dicts(cur, cur.fetchall())
            for lvl in level_rows:
                lvl.pop('simulation_id', None)
                lvl.pop('level_id', None)

            cur.execute(
                "SELECT * FROM dynamo.yarn_simulation_output WHERE simulation_id = %s",
                (simulation_id,)
            )
            output_row = _row_to_dict(cur, cur.fetchone())
            if output_row:
                output_row.pop('simulation_id', None)

            cur.close()
            conn.close()

            input_row['simulation_id'] = str(input_row['simulation_id'])
            return jsonify(_hydrate_record(input_row, level_rows, output_row))

        except Exception as e:
            logger.error(f"Error fetching yarn simulation {simulation_id}: {e}")
            if conn:
                conn.close()
            return {'error': str(e)}, 500

    def patch(self, simulation_id):
        """
        Update the simulation output. Called by the simulator after computation.

        Body (JSON):
          simulationOutput: {
            simulationCompleted: bool,
            elongations: { unit, value: [...] },
            forces: { unit, value: [...] },
            visualizationFiles: [...]
          }
        Any subset of these fields can be provided.
        """
        body = request.get_json(silent=True) or {}
        sim_output = body.get('simulationOutput')
        if not sim_output:
            return {'error': "Missing 'simulationOutput'"}, 400

        elongations = sim_output.get('elongations') or {}
        forces = sim_output.get('forces') or {}

        update_fields = {}
        if 'simulationCompleted' in sim_output:
            update_fields['simulation_completed'] = sim_output['simulationCompleted']
        if 'unit' in elongations or 'value' in elongations:
            update_fields['elongations_unit'] = elongations.get('unit')
            update_fields['elongations_values'] = Json(elongations.get('value'))
        if 'unit' in forces or 'value' in forces:
            update_fields['forces_unit'] = forces.get('unit')
            update_fields['forces_values'] = Json(forces.get('value'))
        if 'visualizationFiles' in sim_output:
            update_fields['visualization_files'] = Json(sim_output['visualizationFiles'])
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
                    f"UPDATE dynamo.yarn_simulation_output SET {set_clause} WHERE simulation_id = %s",
                    tuple(params)
                )
                if cur.rowcount == 0:
                    return {'error': f"Simulation '{simulation_id}' not found"}, 404

                cur.execute(
                    "UPDATE dynamo.yarn_simulation_input SET updated_at = %s WHERE simulation_id = %s",
                    (now, simulation_id)
                )

                cur.execute(
                    "SELECT * FROM dynamo.yarn_simulation_output WHERE simulation_id = %s",
                    (simulation_id,)
                )
                refreshed = _row_to_dict(cur, cur.fetchone())

            output_event = {
                'simulation_id': simulation_id,
                'simulation_completed': bool(refreshed.get('simulation_completed')),
                'elongations_unit': refreshed.get('elongations_unit'),
                'elongations_values': json.dumps(refreshed['elongations_values']) if refreshed.get('elongations_values') is not None else None,
                'forces_unit': refreshed.get('forces_unit'),
                'forces_values': json.dumps(refreshed['forces_values']) if refreshed.get('forces_values') is not None else None,
                'visualization_files': json.dumps(refreshed['visualization_files']) if refreshed.get('visualization_files') is not None else None,
                'updated_at': refreshed['updated_at'],
            }
            if not send_avro_message(
                TOPIC_DYNAMO_YARN_SIMULATION_OUTPUTS, simulation_id, output_event, OUTPUT_AVRO_SCHEMA
            ):
                logger.warning(f"Output for {simulation_id} updated but Avro publish failed.")

            return {'message': 'Output updated.', 'simulation_id': simulation_id}, 200

        except Exception as e:
            logger.error(f"Failed to update yarn simulation output {simulation_id}: {e}")
            return {'error': str(e)}, 500


# ---------- visualization resource ----------

VISUALIZATION_OBJECT_KEY = 'visualization.glb'


def _minio_object_exists(bucket: str, key: str) -> bool:
    try:
        minio_client.stat_object(bucket, key)
        return True
    except S3Error as e:
        if e.code in ('NoSuchKey', 'NoSuchObject'):
            return False
        raise


class YarnSimulationVisualizationResource(Resource):
    """
    GET /dynamo/yarn-simulations/<simulation_id>/visualization.glb

    Streams a single GLB with morph-target animation built from every OBJ
    listed in `simulationOutput.visualizationFiles`. The result is cached in
    MinIO at `yarn-simulations/<simulation_id>/visualization.glb` so the
    expensive merge runs only once per file set.
    """
    method_decorators = [require_api_key]

    def get(self, simulation_id):
        conn = None
        try:
            # 1. Fetch the simulation's visualization file list from Postgres.
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT visualization_files, simulation_completed "
                "FROM dynamo.yarn_simulation_output WHERE simulation_id = %s",
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
                return {'error': "Simulation has no visualization files."}, 404
            if len(viz_files) < 2:
                return {'error': "Need at least 2 OBJ frames to build an animation."}, 422

            cache_key = f"{simulation_id}/{VISUALIZATION_OBJECT_KEY}"

            # 2. Serve from MinIO cache if present.
            if _minio_object_exists(MINIO_YARN_SIMULATION_BUCKET, cache_key):
                logger.info(f"[yarn-viz] cache HIT {cache_key}")
                obj = minio_client.get_object(MINIO_YARN_SIMULATION_BUCKET, cache_key)
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

            # 3. Cache miss: download every OBJ, build GLB, upload result, serve.
            logger.info(f"[yarn-viz] cache MISS — building from {len(viz_files)} OBJ frames")
            obj_texts = []
            for filename in viz_files:
                key = f"{simulation_id}/{filename}"
                logger.info(f"[yarn-viz] fetching {key}")
                obj = minio_client.get_object(MINIO_YARN_SIMULATION_BUCKET, key)
                try:
                    obj_texts.append(obj.read().decode('utf-8'))
                finally:
                    obj.close()
                    obj.release_conn()

            glb_bytes = build_morph_target_glb(obj_texts)

            # Persist back to MinIO for subsequent requests.
            minio_client.put_object(
                MINIO_YARN_SIMULATION_BUCKET,
                cache_key,
                io.BytesIO(glb_bytes),
                length=len(glb_bytes),
                content_type='model/gltf-binary',
            )
            logger.info(f"[yarn-viz] cached {cache_key} ({len(glb_bytes)} bytes)")

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
            logger.error(f"[yarn-viz] MinIO error for {simulation_id}: {e}")
            return {'error': 'Storage error', 'message': str(e)}, 502
        except ValueError as e:
            logger.error(f"[yarn-viz] build error for {simulation_id}: {e}")
            return {'error': str(e)}, 422
        except Exception as e:
            logger.exception(f"[yarn-viz] failed for {simulation_id}: {e}")
            if conn:
                conn.close()
            return {'error': str(e)}, 500
