import os
import json
import logging
from typing import Any, Dict
from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import SerializationContext, MessageField

logger = logging.getLogger(__name__)

# Configuration
KAFKA_BROKER = os.environ.get('KAFKA_BROKER', 'kafka:29092')
SCHEMA_REGISTRY_URL = os.environ.get('SCHEMA_REGISTRY_URL', 'http://schema-registry:8081')

# Topic Constants
TOPIC_ARTIFACTS = 'artifacts'
TOPIC_SENSOR_READINGS = 'sensor_readings'
TOPIC_ARTIFACT_UPLOADED = 'artifact_uploaded'
TOPIC_SENSOR_UPLOADED = 'sensor_reading_uploaded'
TOPIC_ROBOT_IMAGES = 'robot_images'
TOPIC_RECONSTRUCTIONS = 'reconstructions'
TOPIC_ROBOT_UPLOADED = 'robot_image_uploaded'

# Clients
simple_producer = Producer({'bootstrap.servers': KAFKA_BROKER})
schema_registry_client = SchemaRegistryClient({'url': SCHEMA_REGISTRY_URL})

def send_avro_message(topic: str, key: str, value: Dict[str, Any], schema_str: str) -> bool:
    """
    Serializes a message using Avro and sends it to Kafka.

    Args:
        topic (str): Kafka topic name.
        key (str): Message key (usually ID) for partitioning.
        value (dict): The data payload matching the schema.
        schema_str (str): The Avro schema definition string.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        avro_serializer = AvroSerializer(schema_registry_client, schema_str)
        serialized_value = avro_serializer(value, SerializationContext(topic, MessageField.VALUE))

        simple_producer.produce(
            topic=topic,
            key=str(key).encode('utf-8'),
            value=serialized_value
        )
        simple_producer.flush()
        logger.info(f"Sent Avro message to {topic}: {key}")
        return True
    except Exception as e:
        logger.error(f"Kafka Avro Error: {e}")
        return False

def send_simple_message(topic: str, key: str, value: Dict[str, Any]) -> bool:
    """
    Sends a simple JSON message (used for lightweight notifications).
    """
    try:
        simple_producer.produce(
            topic=topic,
            key=str(key).encode('utf-8'),
            value=json.dumps(value).encode('utf-8')
        )
        simple_producer.flush()
        return True
    except Exception as e:
        logger.error(f"Kafka Simple Error: {e}")
        return False