"""
DynaMo Flask wrapper.

POST /run-yarn
    body:   { "simulation_id": "<uuid>", "io_json": { ...DynaMo IO.json... } }
    200:    { "simulationOutput": { ... } }     after OBJs are in MinIO
    422:    { "error": "...", "log_tail": "..." } DynaMo input/validation issue
    500:    { "error": "...", "log_tail": "..." } DynaMo runtime crash

Each request gets a fresh tempdir as CWD so concurrent requests don't fight
over DynaMo's relative-path output folders (DynaMo_Yarn_Simulation/, etc.).
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

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False,
)

app = Flask(__name__)


def _upload_objs_in_order(simulation_id: str, workdir: Path, viz_file_paths: list[str]) -> list[str]:
    """Upload OBJs in the order DynaMo recorded them (init first, then step 0,1,...).
    `viz_file_paths` is `simulationOutput.visualizationFiles` from DynaMo's
    updated IO.json — a mix of relative ("DynaMo_Yarn_Visualization/..._init.obj")
    and absolute paths. We resolve each against the workdir, upload it to
    yarn-simulations/<sim_id>/<basename>, and return the basenames in the
    same order so the GLB morph-target builder animates them correctly.
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
            MINIO_YARN_SIMULATION_BUCKET,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type='model/obj',
        )
        uploaded.append(basename)
        logger.info(f'[{simulation_id}] uploaded {key} ({len(data)} bytes)')
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
        uploaded = _upload_objs_in_order(simulation_id, workdir, viz_paths_from_dynamo)
        sim_output['visualizationFiles'] = uploaded

        logger.info(f'[{simulation_id}] done: {len(uploaded)} OBJ frames uploaded')
        return jsonify({'simulationOutput': sim_output}), 200

    finally:
        shutil.rmtree(workdir, ignore_errors=True)
