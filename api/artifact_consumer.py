import json
import logging
import os
from confluent_kafka import Consumer
from datetime import datetime, timezone
from services.database import get_db_connection

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Config
KAFKA_BROKER = os.environ.get('KAFKA_BROKER', 'kafka:29092')
TOPIC = 'artifact_uploaded'
GROUP_ID = 'artifact-notification-consumer'

def handle_notification(data: dict):
    """
    Updates the 'timestamp_update' field in Postgres when an artifact is uploaded.

    Args:
        data (dict): Data received from the Kafka consumer.
    """
    artifact_id = data.get('artifact_id')
    if not artifact_id:
        return

    try:
        conn = get_db_connection()

        with conn.cursor() as cur:
            timestamp_update = datetime.now(timezone.utc)
            cur.execute(
                "UPDATE artifacts SET timestamp_update = %s WHERE artifact_id = %s",
                (datetime.now(timezone.utc), artifact_id)
            )
            if cur.rowcount == 0:
                logger.warning(f"Artifact {artifact_id} not found for timestamp update")
                return
            conn.commit()
            logger.info(f"Updated timestamp for: {artifact_id}")
        conn.close()

    except Exception as e:
        logger.error(f"DB Error: {e}")

def run_consumer():
    """Main loop for consuming Kafka messages."""
    consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': GROUP_ID,
        'auto.offset.reset': 'earliest'
    })
    consumer.subscribe([TOPIC])
    logger.info(f"Listening on {TOPIC}...")

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None: continue
            if msg.error():
                logger.error(f"Kafka Error: {msg.error()}")
                continue

            try:
                data = json.loads(msg.value().decode('utf-8'))
                handle_notification(data)
            except Exception as e:
                logger.error(f"Processing Error: {e}")
    finally:
        consumer.close()

if __name__ == '__main__':
    run_consumer()