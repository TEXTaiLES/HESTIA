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
    MINIO_AMALTHAI_DATASETS_BUCKET,
    MINIO_AMALTHAI_MODELS_BUCKET,
    MINIO_AMALTHAI_INFERENCE_BUCKET,
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

        cur.execute("SELECT to_regclass('public.artifacts')")
        artifacts_exists = cur.fetchone()[0] is not None

        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name='artifacts' AND column_name='timestamp_update'
        """)
        has_timestamp_update = cur.fetchone() is not None
        if artifacts_exists and not has_timestamp_update:
            logger.info("Migration: Adding 'timestamp_update' column.")
            cur.execute("""
                ALTER TABLE artifacts
                ADD COLUMN timestamp_update TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            """)
        else:
            logger.info("Migration: skipping 'timestamp_update' (artifacts absent or column present).")

        # annotations & reconstructions.artifact_id — resource code (POST /annotations)
        # INSERTs and SELECTs artifact_id but these tables are created by Kafka JDBC
        # sinks whose schemas didn't originally include the column. Add it here so
        # the resource's SQL matches the physical schema.
        for table in ('annotations', 'reconstructions'):
            cur.execute("SELECT to_regclass(%s)", (f'public.{table}',))
            if cur.fetchone()[0] is not None:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS artifact_id TEXT")
                logger.info(f"Migration: ensured {table}.artifact_id column.")
            else:
                logger.info(f"Migration: skipping {table}.artifact_id (table absent).")

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
                artifact_id   UUID,
                created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
            )
        """)
        cur.execute("ALTER TABLE nefele_jobs ADD COLUMN IF NOT EXISTS artifact_id UUID")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_nefele_jobs_status ON nefele_jobs (status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_nefele_jobs_artifact_id ON nefele_jobs (artifact_id)")
        logger.info("Migration: nefele_jobs table ensured.")

        # AmalthAI integration tables — mutable registry/job rows (PATCH-heavy),
        # created with explicit DDL (NOT via Kafka JDBC sinks).
        # Order matters: datasets -> models -> experiments/inference so FKs resolve.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS amalthai_datasets (
                dataset_id    UUID         PRIMARY KEY,
                owner_slug    TEXT         NOT NULL,
                owner_email   TEXT,
                name          TEXT         NOT NULL,
                mode          TEXT         NOT NULL,
                num_classes   INTEGER,
                content_hash  TEXT,
                object_key    TEXT,
                archive_url   TEXT,
                manifest      JSONB,
                size_bytes    BIGINT,
                linked_scan_id           TEXT,
                linked_artifact_id       TEXT,
                linked_reconstruction_id TEXT,
                status        TEXT         NOT NULL DEFAULT 'ready',
                created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_amalthai_datasets_owner ON amalthai_datasets (owner_slug)")
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_amalthai_datasets_owner_mode_name
            ON amalthai_datasets (owner_slug, mode, name)
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS amalthai_models (
                model_id      UUID         PRIMARY KEY,
                owner_slug    TEXT         NOT NULL,
                owner_email   TEXT,
                name          TEXT         NOT NULL,
                mode          TEXT         NOT NULL,
                trained_on    TEXT,
                dataset_id    UUID         REFERENCES amalthai_datasets(dataset_id),
                experiment_id UUID,
                score         DOUBLE PRECISION,
                metric_name   TEXT,
                trained_date  TEXT,
                weights_key   TEXT,
                weights_url   TEXT,
                config_key    TEXT,
                config_url    TEXT,
                content_hash  TEXT,
                extra         JSONB,
                status        TEXT         NOT NULL DEFAULT 'ready',
                created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_amalthai_models_owner_mode ON amalthai_models (owner_slug, mode)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS amalthai_experiments (
                experiment_id   UUID         PRIMARY KEY,
                owner_slug      TEXT         NOT NULL,
                owner_email     TEXT,
                mode            TEXT         NOT NULL,
                dataset_id      UUID         REFERENCES amalthai_datasets(dataset_id),
                dataset_name    TEXT,
                requested_model TEXT,
                params          JSONB,
                instructions    JSONB,
                metrics         JSONB,
                result_model_id UUID         REFERENCES amalthai_models(model_id),
                job_id          TEXT,
                status          TEXT         NOT NULL DEFAULT 'submitted',
                error           TEXT,
                created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_amalthai_experiments_owner ON amalthai_experiments (owner_slug)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_amalthai_experiments_status ON amalthai_experiments (status)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS amalthai_inference_runs (
                inference_id  UUID         PRIMARY KEY,
                owner_slug    TEXT         NOT NULL,
                owner_email   TEXT,
                mode          TEXT         NOT NULL,
                model_id      UUID         REFERENCES amalthai_models(model_id),
                model_name    TEXT,
                dataset_name  TEXT,
                inputs        JSONB,
                outputs       JSONB,
                input_prefix  TEXT,
                output_prefix TEXT,
                extra         JSONB,
                status        TEXT         NOT NULL DEFAULT 'completed',
                created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_amalthai_inference_owner ON amalthai_inference_runs (owner_slug)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_amalthai_inference_model ON amalthai_inference_runs (model_id)")
        logger.info("Migration: amalthai_* tables ensured.")

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

    # Setup AmalthAI Buckets (Public)
    init_minio_bucket(MINIO_AMALTHAI_DATASETS_BUCKET)
    set_public_read_policy(MINIO_AMALTHAI_DATASETS_BUCKET)
    init_minio_bucket(MINIO_AMALTHAI_MODELS_BUCKET)
    set_public_read_policy(MINIO_AMALTHAI_MODELS_BUCKET)
    init_minio_bucket(MINIO_AMALTHAI_INFERENCE_BUCKET)
    set_public_read_policy(MINIO_AMALTHAI_INFERENCE_BUCKET)

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
