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
    MINIO_THREAD_SIMULATION_BUCKET,
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
        logger.info("Migration: Ensuring dynamo schema exists.")
        cur.execute("CREATE SCHEMA IF NOT EXISTS dynamo;")

        # Yarn → Thread migration. The old yarn_simulation_* tables have no
        # column-level counterpart in the new Thread schema (per-level cores +
        # outers replaced by a single structureInput block), so we drop them
        # entirely and start fresh. Any dev data on these tables is discarded.
        logger.info("Migration: Dropping legacy yarn_simulation_* tables.")
        cur.execute("DROP TABLE IF EXISTS dynamo.yarn_simulation_output CASCADE;")
        cur.execute("DROP TABLE IF EXISTS dynamo.yarn_simulation_input_level CASCADE;")
        cur.execute("DROP TABLE IF EXISTS dynamo.yarn_simulation_input CASCADE;")

        # Thread simulation tables — new flat schema (TEXTaiLES_DynaMo_Thread.scheme.json).
        # Ply-level columns are nullable and only populated when hierarchy_level = 2.
        logger.info("Migration: Ensuring dynamo thread-simulation tables exist.")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dynamo.thread_simulation_input (
                simulation_id UUID PRIMARY KEY,
                artefact_id INTEGER,
                experiment_id INTEGER,
                structure_type TEXT NOT NULL DEFAULT 'Thread',
                friction_value REAL,
                friction_unit TEXT,
                adhesion_value REAL,
                adhesion_unit TEXT,
                discretization_period_count INTEGER,
                discretization_nodes_per_period_count INTEGER,
                applied_elongation_value REAL,
                applied_elongation_unit TEXT,
                hierarchy_level INTEGER NOT NULL,
                thread_total_diameter_value REAL,
                thread_total_diameter_unit TEXT,
                thread_twist_direction TEXT,
                thread_pitch_value REAL,
                thread_pitch_unit TEXT,
                thread_fold_count INTEGER,
                single_yarn_diameter_value REAL,
                single_yarn_diameter_unit TEXT,
                single_yarn_material TEXT,
                single_yarn_youngs_modulus_value REAL,
                single_yarn_youngs_modulus_unit TEXT,
                single_yarn_poisson_ratio_value REAL,
                single_yarn_poisson_ratio_unit TEXT,
                ply_total_diameter_value REAL,
                ply_total_diameter_unit TEXT,
                ply_twist_direction TEXT,
                ply_pitch_value REAL,
                ply_pitch_unit TEXT,
                ply_fold_count INTEGER,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dynamo.thread_simulation_output (
                simulation_id UUID PRIMARY KEY
                    REFERENCES dynamo.thread_simulation_input(simulation_id) ON DELETE CASCADE,
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

        # Dynamo patch-simulation tables — 2 tables: warp/weft are fixed sides,
        # flattened into warp_* / weft_* column prefixes (no child table).
        # New Patch schema renames Radius→Diameter and adds 5 output arrays
        # for stiffness metrics + polar plot data.
        logger.info("Migration: Ensuring dynamo patch-simulation tables exist.")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS dynamo.patch_simulation_input (
                simulation_id UUID PRIMARY KEY,
                artefact_id INTEGER,
                experiment_id INTEGER,
                structure_type TEXT NOT NULL DEFAULT 'Patch',
                weave_pattern TEXT,
                pattern_repetition_count_warp INTEGER,
                pattern_repetition_count_weft INTEGER,
                warp_material TEXT,
                warp_youngs_modulus_value REAL,
                warp_youngs_modulus_unit TEXT,
                warp_poisson_ratio_value REAL,
                warp_poisson_ratio_unit TEXT,
                warp_yarn_diameter_value REAL,
                warp_yarn_diameter_unit TEXT,
                warp_yarn_diameter_ratio_value REAL,
                warp_yarn_diameter_ratio_unit TEXT,
                warp_yarn_count_per_distance_value REAL,
                warp_yarn_count_per_distance_unit TEXT,
                warp_yarn_friction_value REAL,
                warp_yarn_friction_unit TEXT,
                weft_material TEXT,
                weft_youngs_modulus_value REAL,
                weft_youngs_modulus_unit TEXT,
                weft_poisson_ratio_value REAL,
                weft_poisson_ratio_unit TEXT,
                weft_yarn_diameter_value REAL,
                weft_yarn_diameter_unit TEXT,
                weft_yarn_diameter_ratio_value REAL,
                weft_yarn_diameter_ratio_unit TEXT,
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
                effective_extensional_stiffness_unit TEXT,
                effective_extensional_stiffness_values JSONB,
                effective_bending_stiffness_unit TEXT,
                effective_bending_stiffness_values JSONB,
                plot_data_angles_unit TEXT,
                plot_data_angles_values JSONB,
                plot_data_extensional_stiffness_unit TEXT,
                plot_data_extensional_stiffness_values JSONB,
                plot_data_bending_stiffness_unit TEXT,
                plot_data_bending_stiffness_values JSONB,
                simulation_error TEXT,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Patch column renames on existing deployments: yarn_radius → yarn_diameter.
        # Idempotent: only rename if the old name still exists AND the new one
        # doesn't (so re-running the migration is a no-op).
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='dynamo' AND table_name='patch_simulation_input'
        """)
        existing_patch_cols = {r[0] for r in cur.fetchall()}
        for side in ('warp', 'weft'):
            for stem in ('yarn_radius_value', 'yarn_radius_unit',
                         'yarn_radius_ratio_value', 'yarn_radius_ratio_unit'):
                old_col = f'{side}_{stem}'
                new_col = old_col.replace('yarn_radius', 'yarn_diameter')
                if old_col in existing_patch_cols and new_col not in existing_patch_cols:
                    cur.execute(f'ALTER TABLE dynamo.patch_simulation_input RENAME COLUMN {old_col} TO {new_col};')
                    logger.info(f'Migration: renamed dynamo.patch_simulation_input.{old_col} → {new_col}')
        # Patch: backfill new stiffness output columns on existing deployments.
        for stem in ('effective_extensional_stiffness', 'effective_bending_stiffness',
                     'plot_data_angles', 'plot_data_extensional_stiffness',
                     'plot_data_bending_stiffness'):
            cur.execute(f'ALTER TABLE dynamo.patch_simulation_output ADD COLUMN IF NOT EXISTS {stem}_unit TEXT;')
            cur.execute(f'ALTER TABLE dynamo.patch_simulation_output ADD COLUMN IF NOT EXISTS {stem}_values JSONB;')
        # Backfill for yarnFriction + simulation_error on existing deployments.
        cur.execute("ALTER TABLE dynamo.patch_simulation_input ADD COLUMN IF NOT EXISTS warp_yarn_friction_value REAL;")
        cur.execute("ALTER TABLE dynamo.patch_simulation_input ADD COLUMN IF NOT EXISTS warp_yarn_friction_unit TEXT;")
        cur.execute("ALTER TABLE dynamo.patch_simulation_input ADD COLUMN IF NOT EXISTS weft_yarn_friction_value REAL;")
        cur.execute("ALTER TABLE dynamo.patch_simulation_input ADD COLUMN IF NOT EXISTS weft_yarn_friction_unit TEXT;")
        cur.execute("ALTER TABLE dynamo.patch_simulation_output ADD COLUMN IF NOT EXISTS simulation_error TEXT;")

        # Per-artefact simulation listing: artefact_id + experiment_id (a
        # per-artefact per-type running counter, e.g. Thread #1, #2, ...).
        # The API requires artefact_id on POST — every simulation is
        # submitted from an artefact page — so all new rows carry it. The
        # DB columns are nullable only to accommodate any pre-migration rows
        # that predate these columns; those rows won't appear in per-artefact
        # lists but are still queryable. The unique index catches concurrent
        # duplicate experiment_ids for the same artefact.
        for table in ('thread_simulation_input', 'patch_simulation_input'):
            cur.execute(f'ALTER TABLE dynamo.{table} ADD COLUMN IF NOT EXISTS artefact_id INTEGER;')
            cur.execute(f'ALTER TABLE dynamo.{table} ADD COLUMN IF NOT EXISTS experiment_id INTEGER;')
            cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_artefact ON dynamo.{table}(artefact_id);')
            cur.execute(f"""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_artefact_experiment
                ON dynamo.{table}(artefact_id, experiment_id)
                WHERE artefact_id IS NOT NULL AND experiment_id IS NOT NULL;
            """)

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

    # Setup Thread Simulation outputs (Public)
    init_minio_bucket(MINIO_THREAD_SIMULATION_BUCKET)
    set_public_read_policy(MINIO_THREAD_SIMULATION_BUCKET)

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
        "dynamo-thread-simulation-sink.json",
        "dynamo-thread-simulation-output-sink.json",
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
