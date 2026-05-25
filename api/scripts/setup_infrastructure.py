import time
import sys
import os
import logging

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import get_db_connection
from services.storage import (
    init_minio_bucket,
    set_public_read_policy,
    MINIO_ARTIFACT_BUCKET,
    MINIO_ROBOT_BUCKET,
    MINIO_RECONSTRUCTION_BUCKET,
    MINIO_NEFELE_BUCKET,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def wait_for_postgres(retries=10, delay=2):
    """Polls Postgres until it is ready."""
    logger.info("Waiting for Postgres...")
    for i in range(retries):
        try:
            conn = get_db_connection()
            conn.close()
            logger.info("Postgres is ready!")
            return True
        except Exception:
            logger.info(f"Postgres not ready yet... ({i+1}/{retries})")
            time.sleep(delay)
    return False

def run_migrations():
    """Runs database schema changes."""
    logger.info("Running migrations...")
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Check and Add 'timestamp_update'
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name='artifacts' AND column_name='timestamp_update'
        """)
        if not cur.fetchone():
            logger.info("Migration: Adding 'timestamp_update' column.")
            cur.execute("""
                ALTER TABLE artifacts
                ADD COLUMN timestamp_update TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            """)
        else:
            logger.info("Migration: 'timestamp_update' column already exists.")


        # Nefele init
        # vm_comms — mutable job state (PATCH-heavy). NOT created by a JDBC sink
        # because the row is updated, not upserted. Explicit migration required.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS nefele_jobs (
                job_id        UUID         PRIMARY KEY,
                scan_id       TEXT         NOT NULL,
                dataset_name  TEXT         NOT NULL,
                model         TEXT         NOT NULL DEFAULT 'sugar',
                points_json   JSONB,
                preview       JSONB,
                instructions  JSONB,
                status        TEXT         NOT NULL DEFAULT 'points_submitted',
                stage         TEXT         DEFAULT '',
                stage_index   INTEGER      DEFAULT -1,
                message       TEXT         DEFAULT '',
                error         TEXT,
                created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_nefele_jobs_status ON nefele_jobs (status)")
        logger.info("Migration: nefele_jobs table ensured.")

        conn.commit()
        cur.close()
        conn.close()
        logger.info("Migrations complete.")
    except Exception as e:
        logger.error(f"Migration failed: {e}")

def setup_minio():
    """Initializes MinIO buckets and policies."""
    logger.info("Setting up MinIO Buckets and Policies...")

    # 1. Setup Legacy/Dummy Artifacts (Public)
    init_minio_bucket(MINIO_ARTIFACT_BUCKET)
    set_public_read_policy(MINIO_ARTIFACT_BUCKET)

    # 2. Setup Robot Captures (Public)
    init_minio_bucket(MINIO_ROBOT_BUCKET)
    set_public_read_policy(MINIO_ROBOT_BUCKET)

    # 3. Setup Reconstructions (Public)
    init_minio_bucket(MINIO_RECONSTRUCTION_BUCKET)
    set_public_read_policy(MINIO_RECONSTRUCTION_BUCKET)

    # Setup Nefele Previews (Public)
    init_minio_bucket(MINIO_NEFELE_BUCKET)
    set_public_read_policy(MINIO_NEFELE_BUCKET)

    logger.info("MinIO setup complete.")

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
                connector_conf = json.load(f)

            connector_class = connector_conf.get('config', {}).get('connector.class', '')

            if 'JdbcSinkConnector' in connector_class:
                logger.info(f"Configuring JDBC settings for {filename}")
                connector_conf['config']['connection.url'] = f"jdbc:postgresql://{PG_HOST}:{PG_PORT}/{PG_DB}?stringtype=unspecified"
                connector_conf['config']['connection.user'] = PG_USER
                connector_conf['config']['connection.password'] = PG_PASSWORD
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
    if wait_for_postgres():
        run_migrations()
        setup_minio()
    else:
        logger.error("Could not connect to database. Setup aborted.")
        sys.exit(1)
