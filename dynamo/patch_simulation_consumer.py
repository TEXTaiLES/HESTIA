"""
Consumes `dynamo_patch_simulation_uploaded` notifications and drives the
DynaMo simulator's patch endpoint via its Flask wrapper.

Pipeline per message:
  1. fetch the simulation from the API
  2. build the DynaMo IO.json
  3. POST it to the dynamo_service Flask wrapper's /run-patch endpoint
  4. PATCH the resulting simulationOutput back to the API
     (or PATCH a failure marker if DynaMo crashed)
"""
import json
import logging
import os

import requests
from confluent_kafka import Consumer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

KAFKA_BROKER = os.environ.get('KAFKA_BROKER', 'kafka:29092')
TOPIC = 'dynamo_patch_simulation_uploaded'
GROUP_ID = 'patch-simulation-consumer'

API_INTERNAL_ENDPOINT = os.environ.get('API_INTERNAL_ENDPOINT', 'api:5000')
API_SECRET_KEY = os.environ.get('API_SECRET_KEY')

DYNAMO_SERVICE_URL = os.environ.get('DYNAMO_SERVICE_URL', 'http://dynamo_service:5001')
DYNAMO_REQUEST_TIMEOUT_SEC = int(os.environ.get('DYNAMO_REQUEST_TIMEOUT_SEC', '600'))

# Same six experiment buckets the Flask service writes; we sum across them
# only for the log line so the operator can see total OBJ count at a glance.
PATCH_VIZ_KEYS = (
    'visualizationFiles_inplane11',
    'visualizationFiles_inplane22',
    'visualizationFiles_inplane12',
    'visualizationFiles_bending11',
    'visualizationFiles_bending22',
    'visualizationFiles_bending12',
)


def _api_headers():
    return {'Authorization': f'Bearer {API_SECRET_KEY}'}


def _fetch_simulation(simulation_id: str) -> dict:
    url = f'http://{API_INTERNAL_ENDPOINT}/dynamo/patch-simulations/{simulation_id}'
    r = requests.get(url, headers=_api_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def _patch_output(simulation_id: str, simulation_output: dict) -> None:
    url = f'http://{API_INTERNAL_ENDPOINT}/dynamo/patch-simulations/{simulation_id}'
    r = requests.patch(
        url,
        headers={**_api_headers(), 'Content-Type': 'application/json'},
        json={'simulationOutput': simulation_output},
        timeout=30,
    )
    r.raise_for_status()


def _build_io_json(sim: dict) -> dict:
    """Strip API metadata and pass only what DynaMo expects.

    DynaMo validates the *starting* IO.json against its full schema, which
    requires simulationOutput.simulationCompleted plus the six visualization
    arrays. We seed empty arrays when the row hasn't been PATCHed yet.
    """
    sim_output = sim.get('simulationOutput') or {}
    out = {
        'structureType': sim.get('structureType', 'Patch'),
        'simulationInput': sim['simulationInput'],
        'simulationOutput': {
            'simulationCompleted': sim_output.get('simulationCompleted', False),
        },
    }
    for viz_key in PATCH_VIZ_KEYS:
        out['simulationOutput'][viz_key] = sim_output.get(viz_key) or []
    return out


def _run_dynamo_via_http(simulation_id: str, io_json: dict) -> tuple[int, dict]:
    """POST IO.json to the dynamo_service Flask wrapper's /run-patch endpoint.
    Returns (status_code, response_json). Network errors surface as 502.
    """
    url = f'{DYNAMO_SERVICE_URL}/run-patch'
    try:
        r = requests.post(
            url,
            json={'simulation_id': simulation_id, 'io_json': io_json},
            timeout=DYNAMO_REQUEST_TIMEOUT_SEC,
        )
    except requests.RequestException as e:
        logger.error(f'[{simulation_id}] dynamo_service unreachable: {e}')
        return 502, {'error': f'dynamo_service unreachable: {e}'}
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {'error': r.text[:500]}


def _patch_failure(simulation_id: str, error_message: str) -> None:
    """Record a failure on the simulation so the portal can stop spinning."""
    try:
        _patch_output(simulation_id, {
            'simulationCompleted': False,
            'simulationError': error_message[:1000],
        })
        logger.info(f'[{simulation_id}] failure marker PATCHed')
    except Exception as e:
        logger.error(f'[{simulation_id}] failure-marker PATCH itself failed: {e}')


def handle_notification(notif: dict) -> None:
    simulation_id = notif.get('simulation_id')
    if not simulation_id:
        logger.warning(f'notification missing simulation_id: {notif}')
        return

    logger.info(f'[{simulation_id}] starting DynaMo patch run')

    try:
        sim = _fetch_simulation(simulation_id)
    except Exception as e:
        logger.error(f'[{simulation_id}] could not fetch via API: {e}')
        return

    io_json = _build_io_json(sim)

    status, payload = _run_dynamo_via_http(simulation_id, io_json)
    if status != 200:
        solver_tail = payload.get('solver_log_tail') or ''
        log_tail = payload.get('log_tail') or payload.get('error') or 'unknown error'
        logger.error(f'[{simulation_id}] DynaMo failed ({status}): {(solver_tail or log_tail)[-500:]}')
        error_text = payload.get('error', 'DynaMo failed')
        if solver_tail:
            _patch_failure(simulation_id, f'{error_text} | solver: {solver_tail[-400:]} | python: {log_tail[-200:]}')
        else:
            _patch_failure(simulation_id, f'{error_text} | {log_tail[-500:]}')
        return

    sim_output = payload.get('simulationOutput') or {}
    total_objs = sum(len(sim_output.get(k) or []) for k in PATCH_VIZ_KEYS)
    logger.info(f'[{simulation_id}] DynaMo finished; {total_objs} OBJ frames across {len(PATCH_VIZ_KEYS)} experiments')

    try:
        _patch_output(simulation_id, sim_output)
    except Exception as e:
        logger.error(f'[{simulation_id}] PATCH failed: {e}')
        return

    logger.info(f'[{simulation_id}] done')


def run_consumer():
    consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': GROUP_ID,
        'auto.offset.reset': 'earliest',
    })
    consumer.subscribe([TOPIC])
    logger.info(f'Listening on {TOPIC}...')

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                logger.error(f'Kafka error: {msg.error()}')
                continue
            try:
                notif = json.loads(msg.value().decode('utf-8'))
                handle_notification(notif)
            except Exception as e:
                logger.error(f'processing error: {e}')
    finally:
        consumer.close()


if __name__ == '__main__':
    run_consumer()
