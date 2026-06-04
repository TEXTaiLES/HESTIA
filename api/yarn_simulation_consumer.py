"""
Consumes `dynamo_yarn_simulation_uploaded` notifications, runs the DynaMo
simulator container against the submission's input, uploads the resulting OBJ
frames to MinIO, and PATCHes the simulation output back via the API.

The simulator container itself is invoked through the Docker SDK using
put_archive / get_archive (no bind mounts) so this worker can run anywhere a
docker socket is reachable.
"""
import io
import json
import logging
import os
import re
import tarfile
from pathlib import Path

import docker
import requests
from confluent_kafka import Consumer

from services.storage import minio_client, MINIO_YARN_SIMULATION_BUCKET

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

KAFKA_BROKER = os.environ.get('KAFKA_BROKER', 'kafka:29092')
TOPIC = 'dynamo_yarn_simulation_uploaded'
GROUP_ID = 'yarn-simulation-consumer'

API_INTERNAL_ENDPOINT = os.environ.get('API_INTERNAL_ENDPOINT', 'api:5000')
API_SECRET_KEY = os.environ.get('API_SECRET_KEY')

DYNAMO_IMAGE = os.environ.get('DYNAMO_IMAGE', 'dynamo:v1')

# Path matching the readme's `-v ./IO_Folder:/working_environment/IO_Folder`.
DYNAMO_IO_PARENT = '/working_environment'
DYNAMO_IO_FOLDER_NAME = 'IO_Folder'


def _api_headers():
    return {'Authorization': f'Bearer {API_SECRET_KEY}'}


def _fetch_simulation(simulation_id: str) -> dict:
    url = f'http://{API_INTERNAL_ENDPOINT}/dynamo/yarn-simulations/{simulation_id}'
    r = requests.get(url, headers=_api_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def _patch_output(simulation_id: str, simulation_output: dict) -> None:
    url = f'http://{API_INTERNAL_ENDPOINT}/dynamo/yarn-simulations/{simulation_id}'
    r = requests.patch(
        url,
        headers={**_api_headers(), 'Content-Type': 'application/json'},
        json={'simulationOutput': simulation_output},
        timeout=30,
    )
    r.raise_for_status()


_LEVEL_KEY_RE = re.compile(r'^inputLevel(\d+)$')


def _rename_levels_for_dynamo(sim_input: dict) -> dict:
    """API stores levels as 1-based `inputLevelN`; DynaMo wants 0-based `inputLevel_N`."""
    out = {}
    for key, value in (sim_input or {}).items():
        m = _LEVEL_KEY_RE.match(key)
        if m:
            out[f'inputLevel_{int(m.group(1)) - 1}'] = value
        else:
            out[key] = value
    return out


def _build_io_json(sim: dict) -> dict:
    """Strip API metadata and pass only what DynaMo expects.

    DynaMo validates the *starting* IO.json against its full schema (incl.
    simulationOutput), and unit fields can't be null — so we seed defaults
    here when the row hasn't been PATCHed yet. Level keys also get renamed
    from the API's 1-based `inputLevelN` to DynaMo's 0-based `inputLevel_N`.
    """
    sim_output = sim.get('simulationOutput') or {}
    elongations = sim_output.get('elongations') or {}
    forces = sim_output.get('forces') or {}
    return {
        'structureType': sim.get('structureType', 'Yarn'),
        'simulationInput': _rename_levels_for_dynamo(sim['simulationInput']),
        'simulationOutput': {
            'simulationCompleted': sim_output.get('simulationCompleted', False),
            'elongations': {
                'unit': elongations.get('unit') or '%',
                'value': elongations.get('value') or [],
            },
            'forces': {
                'unit': forces.get('unit') or 'N',
                'value': forces.get('value') or [],
            },
            'visualizationFiles': sim_output.get('visualizationFiles') or [],
        },
    }


def _io_json_to_tar(io_json: dict) -> bytes:
    """Tar containing IO_Folder/IO.json, suitable for container.put_archive
    at /working_environment/. DynaMo creates its own output subfolders
    (DynaMo_Yarn_Visualization/, DynaMo_Yarn_Simulation/) at runtime.
    """
    payload = json.dumps(io_json).encode('utf-8')
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tar:
        info = tarfile.TarInfo(name=f'{DYNAMO_IO_FOLDER_NAME}/IO.json')
        info.size = len(payload)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def _extract_dynamo_artifacts(archive_bytes: bytes):
    """
    Walk the tar streamed back from the container and pull out:
      - the updated IO.json (returned as dict)
      - every *.obj file produced by DynaMo (returned as {filename: bytes})
    """
    updated_io = None
    viz_files: dict[str, bytes] = {}

    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode='r') as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            data = f.read()

            name = member.name
            base = Path(name).name

            if base == 'IO.json':
                updated_io = json.loads(data.decode('utf-8'))
            elif base.endswith('.obj'):
                viz_files[base] = data

    if updated_io is None:
        raise RuntimeError('DynaMo container did not produce IO.json')

    return updated_io, viz_files


def _run_dynamo(io_json: dict):
    """
    Spawn `dynamo:v1`, push IO.json in, wait, pull results out.
    Returns (updated_io_json, viz_files_dict).
    """
    client = docker.from_env()

    logger.info(f"Pulling/locating image {DYNAMO_IMAGE}")
    container = client.containers.create(DYNAMO_IMAGE)
    logger.info(f"Created container {container.short_id}")

    try:
        tar_bytes = _io_json_to_tar(io_json)
        container.put_archive(DYNAMO_IO_PARENT, tar_bytes)

        container.start()
        result = container.wait()
        exit_code = result.get('StatusCode', -1)
        if exit_code != 0:
            logs = container.logs().decode('utf-8', errors='replace')
            raise RuntimeError(f'DynaMo exited {exit_code}: {logs[-2000:]}')

        archive_iter, _ = container.get_archive(f'{DYNAMO_IO_PARENT}/{DYNAMO_IO_FOLDER_NAME}')
        archive_bytes = b''.join(archive_iter)
        return _extract_dynamo_artifacts(archive_bytes)
    finally:
        try:
            container.remove(force=True)
        except Exception as e:
            logger.warning(f"container cleanup failed (non-fatal): {e}")


def _upload_obj_files(simulation_id: str, viz_files: dict) -> None:
    for filename, data in viz_files.items():
        key = f'{simulation_id}/{filename}'
        minio_client.put_object(
            MINIO_YARN_SIMULATION_BUCKET,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type='model/obj',
        )
        logger.info(f"uploaded {key} ({len(data)} bytes)")


def handle_notification(notif: dict) -> None:
    simulation_id = notif.get('simulation_id')
    if not simulation_id:
        logger.warning(f"notification missing simulation_id: {notif}")
        return

    logger.info(f"[{simulation_id}] starting DynaMo run")

    try:
        sim = _fetch_simulation(simulation_id)
    except Exception as e:
        logger.error(f"[{simulation_id}] could not fetch via API: {e}")
        return

    io_json = _build_io_json(sim)

    try:
        updated_io, viz_files = _run_dynamo(io_json)
    except Exception as e:
        logger.error(f"[{simulation_id}] DynaMo failed: {e}")
        return

    logger.info(f"[{simulation_id}] DynaMo finished; {len(viz_files)} OBJ frames")

    try:
        _upload_obj_files(simulation_id, viz_files)
    except Exception as e:
        logger.error(f"[{simulation_id}] OBJ upload failed: {e}")
        return

    # Normalize visualizationFiles to basenames matching the MinIO keys we
    # just wrote (`<sim_id>/<basename>`). DynaMo writes them as relative
    # paths like `visualization/yarn_0.obj`; the GLB builder concatenates
    # `<sim_id>/<entry>` directly, so anything other than basenames breaks
    # the lookup.
    sim_output = updated_io.get('simulationOutput') or {}
    viz_from_io = sim_output.get('visualizationFiles') or []
    if viz_from_io:
        sim_output['visualizationFiles'] = [Path(p).name for p in viz_from_io]
    else:
        sim_output['visualizationFiles'] = sorted(viz_files.keys())

    try:
        _patch_output(simulation_id, sim_output)
    except Exception as e:
        logger.error(f"[{simulation_id}] PATCH failed: {e}")
        return

    logger.info(f"[{simulation_id}] done")


def run_consumer():
    consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': GROUP_ID,
        'auto.offset.reset': 'earliest',
    })
    consumer.subscribe([TOPIC])
    logger.info(f"Listening on {TOPIC}...")

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                logger.error(f"Kafka error: {msg.error()}")
                continue
            try:
                notif = json.loads(msg.value().decode('utf-8'))
                handle_notification(notif)
            except Exception as e:
                logger.error(f"processing error: {e}")
    finally:
        consumer.close()


if __name__ == '__main__':
    run_consumer()