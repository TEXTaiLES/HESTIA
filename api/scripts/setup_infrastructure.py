import time
import sys
import os
import logging
import json
import requests
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import (
    get_db_connection,
    PG_DB,
    PG_USER,
    PG_PASSWORD
)
from services.storage import (
    init_minio_bucket,
    set_public_read_policy,
    MINIO_ARTIFACT_BUCKET,
    MINIO_ROBOT_BUCKET,
    MINIO_RECONSTRUCTION_BUCKET
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
KAFKA_CONNECT_URL = os.getenv('KAFKA_CONNECT_URL', "http://kafka-connect:8083")
CONNECTORS_DIR = Path(os.getenv('CONNECTORS_DIR', "/app/connectors"))

def wait_for_service(name, check_func, retries=60, delay=2):
    """Generic waiter for dependent services."""
    logger.info(f"Waiting for {name}...")
    for i in range(retries):
        try:
            if check_func():
                logger.info(f"{name} is ready!")
                return True
        except Exception:
            pass
        time.sleep(delay)
    logger.error(f"Timeout waiting for {name}.")
    return False

def check_postgres():
    conn = get_db_connection()
    conn.close()
    return True

def check_kafka_connect():
    try:
        resp = requests.get(f"{KAFKA_CONNECT_URL}/")
        return resp.status_code == 200
    except:
        return False

def init_database_schema():
    """Explicitly create tables with strict typing and constraints."""
    logger.info("Initializing Database Schema...")
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # 1. Reconstructions Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reconstructions (
                object_id TEXT PRIMARY KEY,
                scan_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                model_location TEXT,
                texture_location TEXT,
                material_location TEXT,
                glb_location TEXT,
                public_url_model TEXT,
                public_url_texture TEXT,
                public_url_material TEXT,
                public_url_glb TEXT,
                timestamp TIMESTAMPTZ NOT NULL
            );
        """)

        # 2. Robot Images Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS robot_images (
                image_id TEXT PRIMARY KEY,
                scan_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                location TEXT NOT NULL,
                public_url TEXT,
                timestamp TIMESTAMPTZ NOT NULL,
                robot_pose TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_robot_scan_id ON robot_images(scan_id);
        """)

        # 3. Annotations Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS annotations (
                scene_id TEXT PRIMARY KEY,
                object_id TEXT NOT NULL,
                public_url TEXT,
                location TEXT,
                collaborative BOOLEAN DEFAULT FALSE,
                content TEXT,
                timestamp TIMESTAMPTZ NOT NULL
            );
        """)

        # 4. Sensor Readings Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                sensor_id TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                temperature DOUBLE PRECISION NOT NULL,
                humidity DOUBLE PRECISION NOT NULL,
                uv_intensity DOUBLE PRECISION,
                luminosity DOUBLE PRECISION,
                atmospheric_pressure INTEGER,
                elevation DOUBLE PRECISION,
                artifact_id TEXT,
                PRIMARY KEY (sensor_id, timestamp)
            );
        """)

        conn.commit()
        cur.close()
        conn.close()
        logger.info("Schema initialization complete.")
    except Exception as e:
        logger.error(f"Schema init failed: {e}")
        raise e

def setup_minio():
    """Initializes MinIO buckets and policies."""
    logger.info("Setting up MinIO Buckets and Policies...")
    try:
        # 1. Setup Legacy/Dummy Artifacts (Public)
        init_minio_bucket(MINIO_ARTIFACT_BUCKET)
        set_public_read_policy(MINIO_ARTIFACT_BUCKET)

        # 2. Setup Robot Captures (Private)
        init_minio_bucket(MINIO_ROBOT_BUCKET)

        # 3. Setup Reconstructions (Public)
        init_minio_bucket(MINIO_RECONSTRUCTION_BUCKET)
        set_public_read_policy(MINIO_RECONSTRUCTION_BUCKET)

        logger.info("MinIO setup complete.")
    except Exception as e:
        logger.error(f"MinIO setup failed: {e}")
        raise e

def register_connectors():
    """Reads JSON files, injects env variables, and registers connectors."""
    logger.info("Registering Kafka Connectors...")

    connector_files = [
        "postgres-sink.json",
        "annotation-sink.json",
        "robot-sink.json",
        "sensor-sink.json",
        "reconstruction-sink.json"
    ]

    for filename in connector_files:
        file_path = CONNECTORS_DIR / filename
        if not file_path.exists():
            logger.warning(f"Connector file not found: {file_path}")
            continue

        try:
            with open(file_path, 'r') as f:
                config_json = f.read()

            # Replace variables
            config_json = config_json.replace("db", PG_DB)
            config_json = config_json.replace("pg_user", PG_USER)
            config_json = config_json.replace("pg_password", PG_PASSWORD)

            connector_conf = json.loads(config_json)
            connector_name = connector_conf.get("name")

            # Check if exists
            resp = requests.get(f"{KAFKA_CONNECT_URL}/connectors/{connector_name}")
            if resp.status_code == 200:
                logger.info(f"Connector {connector_name} already exists. Updating...")
                requests.put(
                    f"{KAFKA_CONNECT_URL}/connectors/{connector_name}/config",
                    json=connector_conf["config"]
                )
            else:
                logger.info(f"Creating connector {connector_name}...")
                resp = requests.post(
                    f"{KAFKA_CONNECT_URL}/connectors",
                    json=connector_conf
                )
                if resp.status_code != 201:
                    raise Exception(f"Failed to create {connector_name}: {resp.text}")

        except Exception as e:
            logger.error(f"Failed to process {filename}: {e}")
            raise e

if __name__ == "__main__":
    try:
        # 1. Wait for Core Infrastructure
        if not wait_for_service("Postgres", check_postgres): sys.exit(1)

        # 2. Setup Database & Storage
        init_database_schema()
        setup_minio()

        # 3. Wait for Kafka Connect
        if not wait_for_service("Kafka Connect", check_kafka_connect, retries=60):
            raise Exception("Kafka Connect never became ready.")

        # 4. Register Connectors
        register_connectors()

        logger.info("Infrastructure Setup Complete!")

    except Exception as e:
        logger.error(f"Setup failed: {e}")
        sys.exit(1)