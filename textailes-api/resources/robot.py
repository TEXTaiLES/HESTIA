from flask import request  # , jsonify
from flask_restful import Resource
from datetime import datetime, timezone
import logging

from middleware.security import require_api_key
# from services.database import get_db_connection
from services.messaging import (
    send_avro_message,
    send_simple_message,
    TOPIC_ROBOT_IMAGES,
    TOPIC_ROBOT_UPLOADED
)

logger = logging.getLogger(__name__)

# Avro Schema Definition
ROBOT_AVRO_SCHEMA = """
{
    "type": "record",
    "name": "RobotImage",
    "namespace": "com.textailes.robot",
    "fields": [
        {"name": "image_id", "type": "string"},
        {"name": "filename", "type": "string"},
        {"name": "location", "type": "string"},
        {"name": "public_url", "type": ["null", "string"], "default": null},
        {"name": "timestamp", "type": "string"},
        {"name": "robot_pose", "type": ["null", "string"], "default": null}
    ]
}
"""


class RobotImageResource(Resource):
    method_decorators = [require_api_key]

    def post(self):
        """
        Ingests a new robot image, validates via Avro, and streams to Kafka.
        """

        data = request.get_json()
        if not data:
            return {'error': 'No data provided'}, 400

        # Validate
        required_fields = ['image_id', 'filename', 'location']
        if not all(k in data for k in required_fields):
            return {'error': f'Missing required fields. Must include: {required_fields}'}, 400

        if 'timestamp' not in data:
            data['timestamp'] = datetime.now(timezone.utc).isoformat()

        message_key = f"{data['image_id']}_{data['timestamp']}"

        # 1. Send to Storage Topic (Avro)
        success = send_avro_message(
            TOPIC_ROBOT_IMAGES,
            message_key,
            data,
            ROBOT_AVRO_SCHEMA
        )

        if success:
            # 2. Notify Listeners (Simple JSON)
            notification = {
                "image_id": data['image_id'],
                "event_type": "robot_image_received",
                "event_timestamp": datetime.now(timezone.utc).isoformat()
            }
            send_simple_message(TOPIC_ROBOT_UPLOADED,
                                message_key, notification)
            return {'message': 'Image(s) received', 'id': message_key}, 201

        return {'error': 'Failed to process image(s)'}, 500
