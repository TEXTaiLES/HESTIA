from flask import request
from datetime import datetime, timezone
import logging

from resources.resource_base import ResourceBase
from services.messaging import (
    send_avro_message,
    send_simple_message,
    TOPIC_SENSOR_READINGS,
    TOPIC_SENSOR_UPLOADED
)

logger = logging.getLogger(__name__)


class SensorReadingResource(ResourceBase):
    avro_schema = """
    {
        "type": "record",
        "name": "SensorReading",
        "namespace": "com.textailes.sensor",
        "fields": [
            {"name": "sensor_id", "type": "string"},
            {"name": "timestamp", "type": "string"},
            {"name": "temperature", "type": "float"},
            {"name": "humidity", "type": "float"},
            {"name": "uv_intensity", "type": ["null", "float"], "default": null},
            {"name": "luminosity", "type": ["null", "float"], "default": null},
            {"name": "atmospheric_pressure", "type": ["null", "int"], "default": null},
            {"name": "elevation", "type": ["null", "float"], "default": null},
            {"name": "artifact_id", "type": ["null", "string"], "default": null}
        ]
    }
    """
    table = "sensor_readings"
    order_by = "timestamp DESC"

    def build_GET_conditions(self):
        conditions = []
        if sensor_id := request.args.get('sensor_id'):
            conditions.append(('sensor_id', '=', sensor_id))
        if start_date := request.args.get('start_date'):
            conditions.append(('timestamp', '>=', start_date))
        if end_date := request.args.get('end_date'):
            conditions.append(('timestamp', '<=', end_date))
        return conditions

    def post(self):
        """
        Ingests a new sensor reading, validates via Avro, and streams to Kafka.
        """
        data = request.get_json()
        if not data:
            return {'error': 'No data provided'}, 400

        # Validate
        required_fields = ['temperature', 'humidity', 'sensor_id']
        if not all(k in data for k in required_fields):
            return {
                'error': f'Missing required fields. Must include: {required_fields}'
            }, 400

        if 'timestamp' not in data:
            data['timestamp'] = datetime.now(timezone.utc).isoformat()

        valid_keys = [
            'sensor_id',
            'timestamp',
            'temperature',
            'humidity',
            'uv_intensity',
            'luminosity',
            'atmospheric_pressure',
            'elevation',
            'artifact_id',
        ]
        if not all([key in valid_keys for key in data.keys()]):
            return {'error': "Not all given keys are valid"}, 400

        message_key = f"{data['sensor_id']}_{data['timestamp']}"

        # Validate with Avro
        if not (success := send_avro_message(
            TOPIC_SENSOR_READINGS,
            message_key,
            data,
            self.avro_schema)
        ):
            return {'error': 'Failed to process reading'}, 500

        # Notify Listeners via Kafka
        notification = {
            "sensor_id": data['sensor_id'],
            "event_type": "sensor_reading_received",
            "event_timestamp": datetime.now(timezone.utc).isoformat()
        }
        send_simple_message(TOPIC_SENSOR_UPLOADED, message_key, notification)
        return {'message': 'Reading received', 'id': message_key}, 201
