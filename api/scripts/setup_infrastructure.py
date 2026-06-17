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
    MINIO_YARN_SIMULATION_BUCKET,
    MINIO_PATCH_SIMULATION_BUCKET,
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
    conn = get_db_connection()
    try:
        cur = conn.cursor()

        # 'artifacts' table is created lazily by the Kafka JDBC sink on first POST,
        # so guard the ALTER and isolate its failure so the rest of the migrations
        # (nefele, dynamo) still run on a clean DB.
        try:
            cur.execute("SELECT to_regclass('public.artifacts')")
            if cur.fetchone()[0] is None:
                logger.info("Migration: 'artifacts' table does not exist yet (Kafka sink pending); skipping 'timestamp_update' migration.")
            else:
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
            conn.commit()
        except Exception as e:
            logger.error(f"'timestamp_update' migration failed (continuing): {e}")
            conn.rollback()


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

        # Dynamo (numerical simulation) tables — kept in their own Postgres schema.
        logger.info("Migration: Ensuring dynamo schema and yarn-simulation tables exist.")
        cur.execute("CREATE SCHEMA IF NOT EXISTS dynamo;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dynamo.yarn_simulation_input (
                simulation_id UUID PRIMARY KEY,
                structure_type TEXT NOT NULL DEFAULT 'Yarn',
                yarn_level_count INTEGER NOT NULL,
                yarn_friction_value REAL,
                yarn_friction_unit TEXT,
                yarn_adhesion_value REAL,
                yarn_adhesion_unit TEXT,
                discretization_period_count INTEGER,
                discretization_nodes_per_period_count INTEGER,
                applied_elongation_value REAL,
                applied_elongation_unit TEXT,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dynamo.yarn_simulation_input_level (
                level_id UUID PRIMARY KEY,
                simulation_id UUID NOT NULL
                    REFERENCES dynamo.yarn_simulation_input(simulation_id) ON DELETE CASCADE,
                level_number INTEGER NOT NULL,
                substructure_count INTEGER,
                twist_core TEXT,
                pitch_core_value REAL,
                pitch_core_unit TEXT,
                material_core TEXT,
                youngs_modulus_core_value REAL,
                youngs_modulus_core_unit TEXT,
                poisson_ratio_core_value REAL,
                poisson_ratio_core_unit TEXT,
                radius_core_value REAL,
                radius_core_unit TEXT,
                outer_strand_count INTEGER,
                twist_outer TEXT,
                pitch_outer_value REAL,
                pitch_outer_unit TEXT,
                material_outer TEXT,
                youngs_modulus_outer_value REAL,
                youngs_modulus_outer_unit TEXT,
                poisson_ratio_outer_value REAL,
                poisson_ratio_outer_unit TEXT,
                radius_outer_value REAL,
                radius_outer_unit TEXT,
                UNIQUE (simulation_id, level_number)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dynamo.yarn_simulation_output (
                simulation_id UUID PRIMARY KEY
                    REFERENCES dynamo.yarn_simulation_input(simulation_id) ON DELETE CASCADE,
                simulation_completed BOOLEAN NOT NULL DEFAULT FALSE,
                elongations_unit TEXT,
                elongations_values JSONB,
                forces_unit TEXT,
                forces_values JSONB,
                visualization_files JSONB,
                simulation_error TEXT,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Backfill column on existing deployments (added when the dynamo simulator
        # link was wired up; lets the consumer record a failure marker on crashes).
        cur.execute("ALTER TABLE dynamo.yarn_simulation_output ADD COLUMN IF NOT EXISTS simulation_error TEXT;")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_yarn_sim_input_level_simulation ON dynamo.yarn_simulation_input_level(simulation_id);")

        # Dynamo patch-simulation tables — 2 tables: warp/weft are fixed sides,
        # flattened into warp_* / weft_* column prefixes (no child table).
        logger.info("Migration: Ensuring dynamo patch-simulation tables exist.")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dynamo.patch_simulation_input (
                simulation_id UUID PRIMARY KEY,
                structure_type TEXT NOT NULL DEFAULT 'Patch',
                weave_pattern TEXT,
                pattern_repetition_count_warp INTEGER,
                pattern_repetition_count_weft INTEGER,
                warp_material TEXT,
                warp_youngs_modulus_value REAL,
                warp_youngs_modulus_unit TEXT,
                warp_poisson_ratio_value REAL,
                warp_poisson_ratio_unit TEXT,
                warp_yarn_radius_value REAL,
                warp_yarn_radius_unit TEXT,
                warp_yarn_radius_ratio_value REAL,
                warp_yarn_radius_ratio_unit TEXT,
                warp_yarn_count_per_distance_value REAL,
                warp_yarn_count_per_distance_unit TEXT,
                warp_yarn_friction_value REAL,
                warp_yarn_friction_unit TEXT,
                weft_material TEXT,
                weft_youngs_modulus_value REAL,
                weft_youngs_modulus_unit TEXT,
                weft_poisson_ratio_value REAL,
                weft_poisson_ratio_unit TEXT,
                weft_yarn_radius_value REAL,
                weft_yarn_radius_unit TEXT,
                weft_yarn_radius_ratio_value REAL,
                weft_yarn_radius_ratio_unit TEXT,
                weft_yarn_count_per_distance_value REAL,
                weft_yarn_count_per_distance_unit TEXT,
                weft_yarn_friction_value REAL,
                weft_yarn_friction_unit TEXT,
                discretization_intermediate_element_count INTEGER,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dynamo.patch_simulation_output (
                simulation_id UUID PRIMARY KEY
                    REFERENCES dynamo.patch_simulation_input(simulation_id) ON DELETE CASCADE,
                simulation_completed BOOLEAN NOT NULL DEFAULT FALSE,
                visualization_files_inplane11 JSONB,
                visualization_files_inplane22 JSONB,
                visualization_files_inplane12 JSONB,
                visualization_files_bending11 JSONB,
                visualization_files_bending22 JSONB,
                visualization_files_bending12 JSONB,
                simulation_error TEXT,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Backfill columns on existing deployments (added alongside the dynamo
        # patch-simulator link). yarnFriction per side is required by DynaMo
        # but missing from the published schema; simulation_error matches the
        # yarn output table.
        cur.execute("ALTER TABLE dynamo.patch_simulation_input ADD COLUMN IF NOT EXISTS warp_yarn_friction_value REAL;")
        cur.execute("ALTER TABLE dynamo.patch_simulation_input ADD COLUMN IF NOT EXISTS warp_yarn_friction_unit TEXT;")
        cur.execute("ALTER TABLE dynamo.patch_simulation_input ADD COLUMN IF NOT EXISTS weft_yarn_friction_value REAL;")
        cur.execute("ALTER TABLE dynamo.patch_simulation_input ADD COLUMN IF NOT EXISTS weft_yarn_friction_unit TEXT;")
        cur.execute("ALTER TABLE dynamo.patch_simulation_output ADD COLUMN IF NOT EXISTS simulation_error TEXT;")

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

    # Setup Yarn Simulation outputs (Public)
    init_minio_bucket(MINIO_YARN_SIMULATION_BUCKET)
    set_public_read_policy(MINIO_YARN_SIMULATION_BUCKET)

    # Setup Patch Simulation outputs (Public)
    init_minio_bucket(MINIO_PATCH_SIMULATION_BUCKET)
    set_public_read_policy(MINIO_PATCH_SIMULATION_BUCKET)

    logger.info("MinIO setup complete.")

def register_connectors():
    """Reads JSON files, injects env variables, and registers connectors."""
    logger.info("Registering Kafka Connectors...")

    connector_files = [
        "postgres-sink.json",
        "annotation-sink.json",
        "robot-sink.json",
        "sensor-sink.json",
        "reconstruction-sink.json",
        "restoration-sink.json",
        "restoration-media-sink.json",
        "restoration-result-sink.json",
        "dynamo-yarn-simulation-sink.json",
        "dynamo-yarn-simulation-level-sink.json",
        "dynamo-yarn-simulation-output-sink.json",
        "dynamo-patch-simulation-sink.json",
        "dynamo-patch-simulation-output-sink.json"
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
