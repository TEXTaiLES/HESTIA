import os
import logging
import psycopg2
from psycopg2.extensions import connection

logger = logging.getLogger(__name__)

# Configuration
PG_HOST = os.environ.get('PG_HOST', 'postgres')
PG_PORT = os.environ.get('PG_PORT', '5432')
PG_DB = os.environ.get('PG_DB')
PG_USER = os.environ.get('PG_USER')
PG_PASSWORD = os.environ.get('PG_PASSWORD')

def get_db_connection() -> connection:
    """
    Creates and returns a new connection to the Postgres database.

    Returns:
        psycopg2.extensions.connection: An active database connection object.

    Raises:
        Exception: If the connection to the database fails.
    """
    try:
        conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            database=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD
        )
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise e