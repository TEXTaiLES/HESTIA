import os
import logging
import psycopg2
from psycopg2.extensions import connection

PG_TABLE_SENSOR_READING = 'sensor_readings'
PG_TABLE_ROBOT_IMAGE = 'robot_images'
PG_TABLE_RECONSTRUCTION = 'reconstructions'
PG_TABLE_ANNOTATION = 'annotations'

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


def insert_record_in_db(record: dict, table_name: str) -> bool:
    """
    Inserts a record in PostgreSQL DB.

    Args:
        record (dict): Record to be inserted in DB.
        table_name (str): Name of the table to be inserted in.

    Returns:
        bool: True if successful, False otherwise.
    """
    if table_name not in [
        PG_TABLE_SENSOR_READING,
        PG_TABLE_ROBOT_IMAGE,
        PG_TABLE_RECONSTRUCTION,
        PG_TABLE_ANNOTATION
    ]:
        logger.error(f"Table '{table_name}' does not exist in DB.")
        return False

    attributes = [key for key in record if record[key]]
    # NOTE: If a '%s' string is used for table_name, the string passed includes
    #       the single_quotes(?).
    sql = f"INSERT INTO {table_name} ({', '.join(attributes)})"
    sql += f" VALUES ({', '.join(['%s'] * len(attributes))})"
    params = [record[attribute] for attribute in attributes]

    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.rowcount > 0
    except Exception as e:
        logger.error(f"Failed to insert record to table '{table_name}'. Error: {str(e)}")
        return False
