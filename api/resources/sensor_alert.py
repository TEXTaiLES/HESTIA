from flask import request
from datetime import datetime, timezone
import uuid
import logging

from resources.resource_base import ResourceBase
from services.database import get_db_connection
from services.utils import fetch_one_dict
from services.messaging import send_simple_message, TOPIC_SENSOR_ALERT_RAISED

logger = logging.getLogger(__name__)


def _parse_timestamp(raw):
    try:
        parsed = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class SensorAlertResource(ResourceBase):
    table = "sensor_alerts"
    order_by = "reading_timestamp DESC"

    def build_GET_conditions(self):
        conditions = []
        if sensor_id := request.args.get('sensor_id'):
            conditions.append(('sensor_id', '=', sensor_id))
        if metric := request.args.get('metric'):
            conditions.append(('metric', '=', metric))
        if start_date := request.args.get('start_date'):
            conditions.append(('reading_timestamp', '>=', start_date))
        if end_date := request.args.get('end_date'):
            conditions.append(('reading_timestamp', '<=', end_date))
        return conditions

    def post(self):
        data = request.get_json(silent=True)
        if not data:
            return {'error': 'No data provided'}, 400

        # Validate
        required_fields = ['sensor_id', 'timestamp', 'metric', 'value']
        if not all(k in data for k in required_fields):
            return {
                'error': f'Missing required fields. Must include: {required_fields}'
            }, 400

        valid_keys = required_fields + ['reading_id', 'min_value', 'max_value']
        if not all([key in valid_keys for key in data.keys()]):
            return {'error': "Not all given keys are valid"}, 400

        if not (reading_timestamp := _parse_timestamp(data['timestamp'])):
            return {'error': 'timestamp must be an ISO-8601 date-time'}, 400
        if not _is_number(data['value']):
            return {'error': 'value must be a number'}, 400
        for bound in ('min_value', 'max_value'):
            if data.get(bound) is not None and not _is_number(data[bound]):
                return {'error': f'{bound} must be a number or null'}, 400

        # Same id format as POST /sensor-readings returns for the reading itself.
        reading_id = data.get('reading_id') or f"{data['sensor_id']}_{data['timestamp']}"

        conn = None
        alert_id = str(uuid.uuid4())
        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO sensor_alerts
                        (alert_id, reading_id, sensor_id, reading_timestamp,
                         metric, value, min_value, max_value)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (sensor_id, reading_timestamp, metric) DO NOTHING
                    RETURNING alert_id
                """, (
                    alert_id,
                    reading_id,
                    data['sensor_id'],
                    reading_timestamp,
                    data['metric'],
                    data['value'],
                    data.get('min_value'),
                    data.get('max_value'),
                ))
                created = cur.fetchone() is not None

                cur.execute("""
                    SELECT * FROM sensor_alerts
                    WHERE sensor_id = %s AND reading_timestamp = %s AND metric = %s
                """, (data['sensor_id'], reading_timestamp, data['metric']))
                row = fetch_one_dict(cur)

            if not created:
                return {'message': 'Alert already recorded',
                        'id': row['alert_id'] if row else None, 'data': row}, 200

            # Notify Listeners via Kafka
            notification = {
                "alert_id": alert_id,
                "sensor_id": data['sensor_id'],
                "metric": data['metric'],
                "event_type": "sensor_alert_raised",
                "event_timestamp": datetime.now(timezone.utc).isoformat()
            }
            send_simple_message(TOPIC_SENSOR_ALERT_RAISED, alert_id, notification)

            return {'message': 'Alert recorded', 'id': alert_id, 'data': row}, 201
        except Exception as e:
            logger.error(f"sensor alert create failed: {e}")
            return {'error': 'Internal server error'}, 500
        finally:
            if conn is not None:
                conn.close()
