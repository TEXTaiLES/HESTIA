import time
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import get_db_connection
from services.storage import init_minio_bucket, set_public_read_policy

def wait_for_postgres(retries=10, delay=2):
    """Polls Postgres until it is ready."""
    print("Waiting for Postgres...")
    for i in range(retries):
        try:
            conn = get_db_connection()
            conn.close()
            print("Postgres is ready!")
            return True
        except Exception:
            print(f"Postgres not ready yet... ({i+1}/{retries})")
            time.sleep(delay)
    return False

def run_migrations():
    """Runs database schema changes."""
    print("Running migrations...")
    print("No active migrations.")

def setup_minio():
    """Initializes MinIO buckets and policies."""
    print("Setting up MinIO...")
    init_minio_bucket()
    set_public_read_policy()
    print("MinIO setup complete.")

if __name__ == "__main__":
    if wait_for_postgres():
        run_migrations()
        setup_minio()
    else:
        print("Could not connect to database. Setup aborted.")
        sys.exit(1)