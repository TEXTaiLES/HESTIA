"""
DynaMo Flask wrapper.

POST /run-yarn
    body:   { "simulation_id": "<uuid>", "io_json": { ...DynaMo Yarn IO.json... } }
    200:    { "simulationOutput": { simulationCompleted, elongations, forces,
                                    visualizationFiles: [basename, ...] } }
    500:    { "error": "...", "log_tail": "...", "solver_log_tail": "..." }
    504:    { "error": "DynaMo timed out after Ns", "log_tail": "..." }

POST /run-patch
    body:   { "simulation_id": "<uuid>", "io_json": { ...DynaMo Patch IO.json... } }
    200:    { "simulationOutput": { simulationCompleted,
                                    visualizationFiles_inplane11: [basename, ...],
                                    visualizationFiles_inplane22: [...],
                                    visualizationFiles_inplane12: [...],
                                    visualizationFiles_bending11: [...],
                                    visualizationFiles_bending22: [...],
                                    visualizationFiles_bending12: [...] } }
    500 / 504: same shape as /run-yarn

Both endpoints invoke the same DynaMo entrypoint; it dispatches on
`structureType` ("Yarn" vs "Patch"). Each request gets a fresh tempdir as
CWD so concurrent requests don't fight over DynaMo's relative-path output
folders (DynaMo_Yarn_Simulation/, DynaMo_Patch_Simulation/, etc.).
"""
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from flask import Flask, jsonify, request
from minio import Minio

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Same entrypoint the dynamo:v1 image invokes by default.
DYNAMO_SCRIPT = '/working_environment/ScriptCollection/InputProjects/TEXTaiLES_DynaMo_Tool.py'

# Bounded so a stuck DynaMo doesn't pin a worker forever (matches gunicorn timeout).
DYNAMO_TIMEOUT_SEC = int(os.environ.get('DYNAMO_TIMEOUT_SEC', '540'))

MINIO_ENDPOINT = os.environ['MINIO_ENDPOINT']
MINIO_ACCESS_KEY = os.environ['MINIO_ACCESS_KEY']
MINIO_SECRET_KEY = os.environ['MINIO_SECRET_KEY']
MINIO_YARN_SIMULATION_BUCKET = os.environ.get('MINIO_YARN_SIMULATION_BUCKET', 'yarn-simulations')
MINIO_PATCH_SIMULATION_BUCKET = os.environ.get('MINIO_PATCH_SIMULATION_BUCKET', 'patch-simulations')

# Patch DynaMo writes 6 disjoint sets of OBJ frames, one per stiffness experiment.
PATCH_VIZ_KEYS = (
    'visualizationFiles_inplane11',
    'visualizationFiles_inplane22',
    'visualizationFiles_inplane12',
    'visualizationFiles_bending11',
    'visualizationFiles_bending22',
    'visualizationFiles_bending12',
)

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False,
)

app = Flask(__name__)


def _upload_objs_in_order(
    simulation_id: str, workdir: Path, viz_file_paths: list[str], bucket: str
) -> list[str]:
    """Upload OBJs in the order DynaMo recorded them.
    `viz_file_paths` is one of the visualizationFiles lists from DynaMo's
    updated IO.json — entries are a mix of relative paths (rooted at the
    sim folder, e.g. "DynaMo_Yarn_Visualization/..._init.obj") and absolute
    paths. We resolve each against the workdir, upload to
    `<bucket>/<sim_id>/<basename>`, and return the basenames in the same
    order so the downstream animation builder preserves frame ordering.
    """
    uploaded = []
    for p in viz_file_paths:
        src = Path(p)
        if not src.is_absolute():
            src = workdir / src
        if not src.exists():
            logger.warning(f'[{simulation_id}] DynaMo listed {p} but file is missing; skipping')
            continue
        basename = src.name
        data = src.read_bytes()
        key = f'{simulation_id}/{basename}'
        minio_client.put_object(
            bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type='model/obj',
        )
        uploaded.append(basename)
        logger.info(f'[{simulation_id}] uploaded {bucket}/{key} ({len(data)} bytes)')
    return uploaded


@app.get('/health')
def health():
    return {'status': 'ok'}, 200


@app.post('/run-yarn')
def run_yarn():
    body = request.get_json(silent=True) or {}
    simulation_id = body.get('simulation_id') or str(uuid.uuid4())
    io_json = body.get('io_json')
    if not io_json:
        return jsonify({'error': "missing 'io_json' in body"}), 400

    logger.info(f'[{simulation_id}] received run-yarn request')

    workdir = Path(tempfile.mkdtemp(prefix=f'dynamo-{simulation_id[:8]}-'))
    try:
        io_path = workdir / 'IO.json'
        io_path.write_text(json.dumps(io_json), encoding='utf-8')

        # Run DynaMo exactly the way the image's default entrypoint does, but
        # with our tempdir as CWD so it writes its DynaMo_Yarn_Simulation/ and
        # DynaMo_Yarn_Visualization/ folders here instead of into the image's
        # /working_environment/IO_Folder.
        try:
            result = subprocess.run(
                ['python3', DYNAMO_SCRIPT, 'IO.json'],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=DYNAMO_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired as e:
            log_tail = (e.stdout or '')[-2000:] if isinstance(e.stdout, str) else ''
            logger.error(f'[{simulation_id}] DynaMo timed out after {DYNAMO_TIMEOUT_SEC}s')
            return jsonify({
                'error': f'DynaMo timed out after {DYNAMO_TIMEOUT_SEC}s',
                'log_tail': log_tail,
            }), 504

        # Widened tail because OBJ-writer subprocesses (TexMathToObj.py) print
        # their errors via the parent's stderr, which can be pushed past 2000
        # chars by the solver's verbose stdout that comes before them.
        log_tail = (result.stdout + '\n' + result.stderr)[-8000:]

        if result.returncode != 0:
            # The Python traceback in stdout only shows the downstream symptom
            # (writeVisualizationFiles failing). The real solver error lives in
            # DynaMo_Yarn_Simulation/run_00/run.log — e.g. "Continuation.deform
            # terminated due to maximal division_level reach". Capture its tail
            # so the simulation_error column has the actual cause, not just the
            # Python wrapper's complaint.
            solver_log = workdir / 'DynaMo_Yarn_Simulation' / 'run_00' / 'run.log'
            solver_log_tail = ''
            if solver_log.exists():
                try:
                    solver_log_tail = solver_log.read_text(encoding='utf-8', errors='replace')[-2000:]
                except Exception as e:
                    logger.warning(f'[{simulation_id}] could not read run.log: {e}')

            logger.error(f'[{simulation_id}] DynaMo exited {result.returncode}')
            # Echo the full captured Python stdout/stderr to the container log
            # so the operator can see post-processing tool errors (e.g.
            # TexMathToObj.py) without needing to query the database.
            logger.error(f'[{simulation_id}] python stdout/stderr tail:\n{log_tail}')
            if solver_log_tail:
                logger.error(f'[{simulation_id}] solver run.log tail:\n{solver_log_tail}')

            return jsonify({
                'error': f'DynaMo exited {result.returncode}',
                'log_tail': log_tail,
                'solver_log_tail': solver_log_tail,
            }), 500

        # Read DynaMo's updated IO.json back.
        try:
            updated_io = json.loads(io_path.read_text(encoding='utf-8'))
        except Exception as e:
            logger.error(f'[{simulation_id}] could not read updated IO.json: {e}')
            return jsonify({'error': 'DynaMo produced no IO.json', 'log_tail': log_tail}), 500

        # Upload OBJs and normalize visualizationFiles to bare basenames so the
        # API's GLB endpoint (which builds `<sim_id>/<entry>` MinIO keys) finds
        # them. We preserve DynaMo's ordering — init first, then step 0,1,2,...
        # — which is what the morph-target animation needs.
        sim_output = updated_io.get('simulationOutput') or {}
        viz_paths_from_dynamo = sim_output.get('visualizationFiles') or []
        uploaded = _upload_objs_in_order(
            simulation_id, workdir, viz_paths_from_dynamo, MINIO_YARN_SIMULATION_BUCKET,
        )
        sim_output['visualizationFiles'] = uploaded

        logger.info(f'[{simulation_id}] done: {len(uploaded)} OBJ frames uploaded')
        return jsonify({'simulationOutput': sim_output}), 200

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.post('/run-patch')
def run_patch():
    body = request.get_json(silent=True) or {}
    simulation_id = body.get('simulation_id') or str(uuid.uuid4())
    io_json = body.get('io_json')
    if not io_json:
        return jsonify({'error': "missing 'io_json' in body"}), 400

    logger.info(f'[{simulation_id}] received run-patch request')

    workdir = Path(tempfile.mkdtemp(prefix=f'dynamo-patch-{simulation_id[:8]}-'))
    try:
        io_path = workdir / 'IO.json'
        io_path.write_text(json.dumps(io_json), encoding='utf-8')

        # Same DynaMo entrypoint as yarn — it dispatches on `structureType`
        # (must be "Patch" in io_json for this code path).
        try:
            result = subprocess.run(
                ['python3', DYNAMO_SCRIPT, 'IO.json'],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=DYNAMO_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired as e:
            log_tail = (e.stdout or '')[-2000:] if isinstance(e.stdout, str) else ''
            logger.error(f'[{simulation_id}] DynaMo timed out after {DYNAMO_TIMEOUT_SEC}s')
            return jsonify({
                'error': f'DynaMo timed out after {DYNAMO_TIMEOUT_SEC}s',
                'log_tail': log_tail,
            }), 504

        log_tail = (result.stdout + '\n' + result.stderr)[-8000:]

        if result.returncode != 0:
            # Patch sims may produce one or more solver run.log files under
            # DynaMo_Patch_Simulation/. Grab the most recent one as a best-effort
            # for the failure-marker; the python tail is the authoritative trace.
            patch_sim_dir = workdir / 'DynaMo_Patch_Simulation'
            solver_log_tail = ''
            if patch_sim_dir.exists():
                candidates = sorted(patch_sim_dir.rglob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
                if candidates:
                    try:
                        solver_log_tail = candidates[0].read_text(encoding='utf-8', errors='replace')[-2000:]
                    except Exception as e:
                        logger.warning(f'[{simulation_id}] could not read {candidates[0]}: {e}')

            logger.error(f'[{simulation_id}] DynaMo exited {result.returncode}')
            logger.error(f'[{simulation_id}] python stdout/stderr tail:\n{log_tail}')
            if solver_log_tail:
                logger.error(f'[{simulation_id}] solver log tail:\n{solver_log_tail}')

            return jsonify({
                'error': f'DynaMo exited {result.returncode}',
                'log_tail': log_tail,
                'solver_log_tail': solver_log_tail,
            }), 500

        try:
            updated_io = json.loads(io_path.read_text(encoding='utf-8'))
        except Exception as e:
            logger.error(f'[{simulation_id}] could not read updated IO.json: {e}')
            return jsonify({'error': 'DynaMo produced no IO.json', 'log_tail': log_tail}), 500

        # Patch has 6 disjoint visualization lists — one per stiffness experiment.
        # Upload each to MinIO with order preserved; the response carries the 6
        # basename arrays so the API's PATCH can store them as-is.
        sim_output = updated_io.get('simulationOutput') or {}
        total_uploaded = 0
        for viz_key in PATCH_VIZ_KEYS:
            viz_paths = sim_output.get(viz_key) or []
            uploaded = _upload_objs_in_order(
                simulation_id, workdir, viz_paths, MINIO_PATCH_SIMULATION_BUCKET,
            )
            sim_output[viz_key] = uploaded
            total_uploaded += len(uploaded)

        logger.info(f'[{simulation_id}] done: {total_uploaded} OBJ frames uploaded across {len(PATCH_VIZ_KEYS)} experiments')
        return jsonify({'simulationOutput': sim_output}), 200

    finally:
        shutil.rmtree(workdir, ignore_errors=True)
