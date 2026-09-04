"""
Thread simulation endpoint (formerly Yarn).

Matches TEXTaiLES_DynaMo_Thread.scheme.json:

  simulationInput:
    friction:            {unit, value}
    adhesion:            {unit, value}
    discretization:      {periodCount, nodesPerPeriodCount}
    appliedElongation:   {unit, value}
    structureInput:
      hierarchyLevel:            1 (plied) or 2 (re-plied)
      threadTotalDiameter:       {unit, value}
      threadTwistDirection:      'S' or 'Z'
      threadPitch:               {unit, value}
      threadFoldCount:           int
      singleYarnDiameter:        {unit, value}
      singleYarnMaterial:        str (optional)
      singleYarnYoungsModulus:   {unit, value}
      singleYarnPoissonRatio:    {unit, value}
      # Only when hierarchyLevel == 2:
      plyTotalDiameter:          {unit, value}
      plyTwistDirection:         'S' or 'Z'
      plyPitch:                  {unit, value}
      plyFoldCount:              int
  simulationOutput:
    simulationCompleted: bool
    elongations:         {unit, value:[]}
    forces:              {unit, value:[]}
    visualizationFiles:  [str]

Single flat DB table for input (no child table — the old inputLevel1..N structure
is gone; the new schema has at most one optional ply block, kept inline as
nullable columns).
"""
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
    TOPIC_DYNAMO_THREAD_SIMULATIONS,
    TOPIC_DYNAMO_THREAD_SIMULATION_OUTPUTS,
    TOPIC_DYNAMO_THREAD_SIMULATION_UPLOADED,
)
from services.storage import minio_client, MINIO_THREAD_SIMULATION_BUCKET
from services.simulation_visualization import build_morph_target_glb
from services.simulation_plots import render_force_elongation_png

logger = logging.getLogger(__name__)

INPUT_FILTERABLE_FIELDS = {'simulation_id', 'structure_type', 'hierarchy_level', 'artefact_id'}

# {unit, value} fields at the top of simulationInput.
TOP_UV_FIELDS = (
    ('friction', 'friction'),
    ('adhesion', 'adhesion'),
    ('appliedElongation', 'applied_elongation'),
)

# {unit, value} fields inside structureInput, always present (level 1 & 2).
STRUCTURE_UV_FIELDS = (
    ('threadTotalDiameter',      'thread_total_diameter'),
    ('threadPitch',              'thread_pitch'),
    ('singleYarnDiameter',       'single_yarn_diameter'),
    ('singleYarnYoungsModulus',  'single_yarn_youngs_modulus'),
    ('singleYarnPoissonRatio',   'single_yarn_poisson_ratio'),
)

# {unit, value} fields inside structureInput, only when hierarchyLevel == 2.
PLY_UV_FIELDS = (
    ('plyTotalDiameter', 'ply_total_diameter'),
    ('plyPitch',         'ply_pitch'),
)


# Avro schema mirrors the flat DB table 1:1. JSONB output columns (elongations,
# forces, visualization_files) ride as Avro `string` carrying JSON; the JDBC
# driver casts string→JSONB.
INPUT_AVRO_SCHEMA = """
{
    "type": "record",
    "name": "ThreadSimulationInput",
    "namespace": "com.textailes.dynamo",
    "fields": [
        {"name": "simulation_id", "type": "string"},
        {"name": "artefact_id", "type": ["null", "int"], "default": null},
        {"name": "experiment_id", "type": ["null", "int"], "default": null},
        {"name": "structure_type", "type": "string"},
        {"name": "friction_value", "type": ["null", "float"], "default": null},
        {"name": "friction_unit", "type": ["null", "string"], "default": null},
        {"name": "adhesion_value", "type": ["null", "float"], "default": null},
        {"name": "adhesion_unit", "type": ["null", "string"], "default": null},
        {"name": "discretization_period_count", "type": ["null", "int"], "default": null},
        {"name": "discretization_nodes_per_period_count", "type": ["null", "int"], "default": null},
        {"name": "applied_elongation_value", "type": ["null", "float"], "default": null},
        {"name": "applied_elongation_unit", "type": ["null", "string"], "default": null},
        {"name": "hierarchy_level", "type": "int"},
        {"name": "thread_total_diameter_value", "type": ["null", "float"], "default": null},
        {"name": "thread_total_diameter_unit", "type": ["null", "string"], "default": null},
        {"name": "thread_twist_direction", "type": ["null", "string"], "default": null},
        {"name": "thread_pitch_value", "type": ["null", "float"], "default": null},
        {"name": "thread_pitch_unit", "type": ["null", "string"], "default": null},
        {"name": "thread_fold_count", "type": ["null", "int"], "default": null},
        {"name": "single_yarn_diameter_value", "type": ["null", "float"], "default": null},
        {"name": "single_yarn_diameter_unit", "type": ["null", "string"], "default": null},
        {"name": "single_yarn_material", "type": ["null", "string"], "default": null},
        {"name": "single_yarn_youngs_modulus_value", "type": ["null", "float"], "default": null},
        {"name": "single_yarn_youngs_modulus_unit", "type": ["null", "string"], "default": null},
        {"name": "single_yarn_poisson_ratio_value", "type": ["null", "float"], "default": null},
        {"name": "single_yarn_poisson_ratio_unit", "type": ["null", "string"], "default": null},
        {"name": "ply_total_diameter_value", "type": ["null", "float"], "default": null},
        {"name": "ply_total_diameter_unit", "type": ["null", "string"], "default": null},
        {"name": "ply_twist_direction", "type": ["null", "string"], "default": null},
        {"name": "ply_pitch_value", "type": ["null", "float"], "default": null},
        {"name": "ply_pitch_unit", "type": ["null", "string"], "default": null},
        {"name": "ply_fold_count", "type": ["null", "int"], "default": null},
        {"name": "created_at", "type": "string"},
        {"name": "updated_at", "type": "string"}
    ]
}
"""

OUTPUT_AVRO_SCHEMA = """
{
    "type": "record",
    "name": "ThreadSimulationOutput",
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


def _build_input_row(body, simulation_id, now, artefact_id, experiment_id):
    """Flatten a POST body's simulationInput + structureInput into flat DB columns.

    artefact_id + experiment_id come from the caller so they can be assigned
    server-side (experiment_id is a per-artefact counter; see POST handler).
    """
    sim_input = body.get('simulationInput') or {}
    structure_input = sim_input.get('structureInput') or {}
    discretization = sim_input.get('discretization') or {}

    row = {
        'simulation_id': simulation_id,
        'artefact_id': artefact_id,
        'experiment_id': experiment_id,
        'structure_type': body.get('structureType', 'Thread'),
        'discretization_period_count': discretization.get('periodCount'),
        'discretization_nodes_per_period_count': discretization.get('nodesPerPeriodCount'),
        'hierarchy_level': structure_input.get('hierarchyLevel'),
        'thread_twist_direction': structure_input.get('threadTwistDirection'),
        'thread_fold_count': structure_input.get('threadFoldCount'),
        'single_yarn_material': structure_input.get('singleYarnMaterial'),
        'ply_twist_direction': structure_input.get('plyTwistDirection'),
        'ply_fold_count': structure_input.get('plyFoldCount'),
        'created_at': now,
        'updated_at': now,
    }
    for json_key, db_prefix in TOP_UV_FIELDS:
        v, u = _uv(sim_input, json_key)
        row[f'{db_prefix}_value'] = v
        row[f'{db_prefix}_unit'] = u
    for json_key, db_prefix in STRUCTURE_UV_FIELDS:
        v, u = _uv(structure_input, json_key)
        row[f'{db_prefix}_value'] = v
        row[f'{db_prefix}_unit'] = u
    for json_key, db_prefix in PLY_UV_FIELDS:
        v, u = _uv(structure_input, json_key)
        row[f'{db_prefix}_value'] = v
        row[f'{db_prefix}_unit'] = u
    return row


def _hydrate_record(input_row, output_row):
    """Rebuild the JSON schema shape from the two flat table rows."""
    structure_input = {
        'hierarchyLevel':       input_row.pop('hierarchy_level', None),
        'threadTwistDirection': input_row.pop('thread_twist_direction', None),
        'threadFoldCount':      input_row.pop('thread_fold_count', None),
        'singleYarnMaterial':   input_row.pop('single_yarn_material', None),
    }
    for json_key, db_prefix in STRUCTURE_UV_FIELDS:
        structure_input[json_key] = _nest_uv(input_row, db_prefix)

    # Ply-level fields only surface when hierarchyLevel == 2. When they're all
    # NULL we still expose them as None so the shape is predictable; the caller
    # can filter based on hierarchyLevel if it wants to.
    ply_twist = input_row.pop('ply_twist_direction', None)
    ply_fold = input_row.pop('ply_fold_count', None)
    structure_input['plyTwistDirection'] = ply_twist
    structure_input['plyFoldCount'] = ply_fold
    for json_key, db_prefix in PLY_UV_FIELDS:
        structure_input[json_key] = _nest_uv(input_row, db_prefix)

    simulation_input = {
        'discretization': {
            'periodCount':         input_row.pop('discretization_period_count', None),
            'nodesPerPeriodCount': input_row.pop('discretization_nodes_per_period_count', None),
        },
        'structureInput': structure_input,
    }
    for json_key, db_prefix in TOP_UV_FIELDS:
        simulation_input[json_key] = _nest_uv(input_row, db_prefix)

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
        'artefact_id': input_row.get('artefact_id'),
        'experiment_id': input_row.get('experiment_id'),
        'structureType': input_row.get('structure_type'),
        'created_at': input_row.get('created_at'),
        'updated_at': input_row.get('updated_at'),
        'simulationInput': simulation_input,
        'simulationOutput': simulation_output,
    }


# ---------- collection resource ----------

class ThreadSimulationResource(Resource):
    method_decorators = [require_api_key]

    def get(self):
        """
        List thread simulations with their nested inputs and outputs.

        Query params:
          simulation_id, structure_type, hierarchy_level — exact-match filters
          page (default 1), per_page (default 50)
        """
        conn = None
        try:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 50))
            offset = (page - 1) * per_page

            sql = "SELECT * FROM dynamo.thread_simulation_input WHERE 1=1"
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
                "SELECT * FROM dynamo.thread_simulation_output WHERE simulation_id = ANY(%s::uuid[])",
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
            logger.error(f"Error fetching thread simulations: {e}")
            if conn:
                conn.close()
            return {'error': str(e)}, 500

    def post(self):
        """
        Create a thread simulation from the portal form. Output starts empty
        (simulation_completed=false, arrays NULL). The simulator populates
        the output later via PATCH on /dynamo/thread-simulations/<simulation_id>.
        """
        body = request.get_json(silent=True)
        if not body:
            return {'error': "No data provided"}, 400

        sim_input = body.get('simulationInput')
        if not sim_input:
            return {'error': "Missing 'simulationInput'"}, 400

        structure_input = sim_input.get('structureInput')
        if not structure_input:
            return {'error': "Missing 'simulationInput.structureInput'"}, 400

        hierarchy_level = structure_input.get('hierarchyLevel')
        if hierarchy_level not in (1, 2):
            return {'error': "structureInput.hierarchyLevel must be 1 or 2"}, 400

        # Every submission comes from an artefact page and is tied to that
        # artefact. experiment_id is assigned server-side (per-artefact
        # counter — Thread #1, #2, ...) so the UI can show a human-friendly
        # identifier alongside the UUID.
        raw_artefact_id = body.get('artefact_id')
        if raw_artefact_id is None:
            return {'error': "Missing 'artefact_id'"}, 400
        try:
            artefact_id = int(raw_artefact_id)
        except (TypeError, ValueError):
            return {'error': "'artefact_id' must be an integer"}, 400

        simulation_id = body.get('simulation_id') or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(experiment_id), 0) + 1 "
                    "FROM dynamo.thread_simulation_input WHERE artefact_id = %s",
                    (artefact_id,)
                )
                experiment_id = cur.fetchone()[0]

                input_row = _build_input_row(body, simulation_id, now, artefact_id, experiment_id)

                cur.execute(
                    f"""
                    INSERT INTO dynamo.thread_simulation_input ({', '.join(input_row.keys())})
                    VALUES ({', '.join(['%s'] * len(input_row))})
                    """,
                    tuple(input_row.values())
                )

                cur.execute(
                    """
                    INSERT INTO dynamo.thread_simulation_output (simulation_id, simulation_completed, updated_at)
                    VALUES (%s, FALSE, %s)
                    """,
                    (simulation_id, now)
                )

            # Publish Avro to Kafka (input + empty output topics).
            input_event = dict(input_row)
            input_event['created_at'] = now_iso
            input_event['updated_at'] = now_iso
            if not send_avro_message(
                TOPIC_DYNAMO_THREAD_SIMULATIONS, simulation_id, input_event, INPUT_AVRO_SCHEMA
            ):
                logger.warning(f"Thread simulation {simulation_id} saved but parent Avro publish failed.")

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
                TOPIC_DYNAMO_THREAD_SIMULATION_OUTPUTS, simulation_id, empty_output_event, OUTPUT_AVRO_SCHEMA
            ):
                logger.warning(f"Output for {simulation_id} saved but Avro publish failed.")

            # Notification — what wakes the simulator.
            if not send_simple_message(
                TOPIC_DYNAMO_THREAD_SIMULATION_UPLOADED,
                simulation_id,
                {'status': 'submitted', 'simulation_id': simulation_id}
            ):
                logger.warning(f"Thread simulation {simulation_id} saved but Kafka notification failed.")

            return {
                'message': "Thread simulation submitted.",
                'simulation_id': simulation_id,
                'artefact_id': artefact_id,
                'experiment_id': experiment_id,
            }, 201

        except Exception as e:
            logger.error(f"Failed to create thread simulation: {e}")
            return {'error': str(e)}, 500


# ---------- item resource ----------

class ThreadSimulationItemResource(Resource):
    method_decorators = [require_api_key]

    def get(self, simulation_id):
        """Fetch a single thread simulation, fully rehydrated."""
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM dynamo.thread_simulation_input WHERE simulation_id = %s",
                (simulation_id,)
            )
            input_row = _row_to_dict(cur, cur.fetchone())
            if not input_row:
                cur.close()
                conn.close()
                return {'error': f"Simulation '{simulation_id}' not found"}, 404

            cur.execute(
                "SELECT * FROM dynamo.thread_simulation_output WHERE simulation_id = %s",
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
            logger.error(f"Error fetching thread simulation {simulation_id}: {e}")
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
                    f"UPDATE dynamo.thread_simulation_output SET {set_clause} WHERE simulation_id = %s",
                    tuple(params)
                )
                if cur.rowcount == 0:
                    return {'error': f"Simulation '{simulation_id}' not found"}, 404

                cur.execute(
                    "UPDATE dynamo.thread_simulation_input SET updated_at = %s WHERE simulation_id = %s",
                    (now, simulation_id)
                )

                cur.execute(
                    "SELECT * FROM dynamo.thread_simulation_output WHERE simulation_id = %s",
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
                TOPIC_DYNAMO_THREAD_SIMULATION_OUTPUTS, simulation_id, output_event, OUTPUT_AVRO_SCHEMA
            ):
                logger.warning(f"Output for {simulation_id} updated but Avro publish failed.")

            return {'message': 'Output updated.', 'simulation_id': simulation_id}, 200

        except Exception as e:
            logger.error(f"Failed to update thread simulation output {simulation_id}: {e}")
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


class ThreadSimulationVisualizationResource(Resource):
    """
    GET /dynamo/thread-simulations/<simulation_id>/visualization.glb

    Streams a single GLB with morph-target animation built from every OBJ
    listed in `simulationOutput.visualizationFiles`. The result is cached in
    MinIO at `thread-simulations/<simulation_id>/visualization.glb` so the
    expensive merge runs only once per file set.
    """
    method_decorators = [require_api_key]

    def get(self, simulation_id):
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT visualization_files, simulation_completed "
                "FROM dynamo.thread_simulation_output WHERE simulation_id = %s",
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

            if _minio_object_exists(MINIO_THREAD_SIMULATION_BUCKET, cache_key):
                logger.info(f"[thread-viz] cache HIT {cache_key}")
                obj = minio_client.get_object(MINIO_THREAD_SIMULATION_BUCKET, cache_key)
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

            logger.info(f"[thread-viz] cache MISS — building from {len(viz_files)} OBJ frames")
            obj_texts = []
            for filename in viz_files:
                key = f"{simulation_id}/{filename}"
                logger.info(f"[thread-viz] fetching {key}")
                obj = minio_client.get_object(MINIO_THREAD_SIMULATION_BUCKET, key)
                try:
                    obj_texts.append(obj.read().decode('utf-8'))
                finally:
                    obj.close()
                    obj.release_conn()

            glb_bytes = build_morph_target_glb(obj_texts)

            minio_client.put_object(
                MINIO_THREAD_SIMULATION_BUCKET,
                cache_key,
                io.BytesIO(glb_bytes),
                length=len(glb_bytes),
                content_type='model/gltf-binary',
            )
            logger.info(f"[thread-viz] cached {cache_key} ({len(glb_bytes)} bytes)")

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
            logger.error(f"[thread-viz] MinIO error for {simulation_id}: {e}")
            return {'error': 'Storage error', 'message': str(e)}, 502
        except ValueError as e:
            logger.error(f"[thread-viz] build error for {simulation_id}: {e}")
            return {'error': str(e)}, 422
        except Exception as e:
            logger.exception(f"[thread-viz] failed for {simulation_id}: {e}")
            if conn:
                conn.close()
            return {'error': str(e)}, 500


# ---------- download resource ----------

class ThreadSimulationDownloadResource(Resource):
    """
    GET /dynamo/thread-simulations/<simulation_id>/download.zip

    Returns a ZIP bundle with:
      - simulation.json         — the full sim record (input + output, hydrated)
      - force_elongation.png    — matplotlib-rendered curve (omitted if arrays missing/empty)
    """
    method_decorators = [require_api_key]

    def get(self, simulation_id):
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM dynamo.thread_simulation_input WHERE simulation_id = %s",
                (simulation_id,)
            )
            input_row = _row_to_dict(cur, cur.fetchone())
            if not input_row:
                cur.close()
                conn.close()
                return {'error': f"Simulation '{simulation_id}' not found"}, 404

            cur.execute(
                "SELECT * FROM dynamo.thread_simulation_output WHERE simulation_id = %s",
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
            logger.error(f"[thread-download] fetch failed for {simulation_id}: {e}")
            if conn:
                conn.close()
            return {'error': str(e)}, 500

        sim_output = record.get('simulationOutput') or {}
        elongations_obj = sim_output.get('elongations') or {}
        forces_obj = sim_output.get('forces') or {}
        png_bytes = render_force_elongation_png(
            elongations_obj.get('value'),
            forces_obj.get('value'),
            elongation_unit=elongations_obj.get('unit') or '%',
            force_unit=forces_obj.get('unit') or 'N',
        )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('simulation.json', json.dumps(record, indent=2, default=str))
            if png_bytes:
                zf.writestr('force_elongation.png', png_bytes)

        return Response(
            buf.getvalue(),
            status=200,
            mimetype='application/zip',
            headers={
                'Content-Disposition': f'attachment; filename="thread-simulation-{simulation_id}.zip"',
                'Cache-Control': 'no-store',
            },
        )
