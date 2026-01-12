import os
import logging
import json
from minio import Minio
from minio.error import S3Error
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Configuration
MINIO_ENDPOINT = os.environ.get('MINIO_ENDPOINT', 'minio:9000')
MINIO_ACCESS_KEY = os.environ.get('MINIO_ACCESS_KEY')
MINIO_SECRET_KEY = os.environ.get('MINIO_SECRET_KEY')
MINIO_ARTIFACT_BUCKET = 'artifacts'
MINIO_ROBOT_BUCKET = 'robot-images'
MINIO_RECONSTRUCTION_BUCKET = 'reconstructions'
MINIO_ANNOTATION_BUCKET = 'annotations'

PUBLIC_MINIO_ENDPOINT = os.environ.get("PUBLIC_MINIO_ENDPOINT", "localhost:9000")
PUBLIC_MINIO_SCHEME = os.environ.get("PUBLIC_MINIO_SCHEME", "https")

# Client Initialization
minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

def set_public_read_policy(bucket_name: str) -> None:
    """
    Sets the bucket policy to allow public read (download) access.
    Required for generating public URLs for artifacts.
    """
    try:
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": "*"},
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{bucket_name}/*"
                }
            ]
        }
        minio_client.set_bucket_policy(bucket_name, json.dumps(policy))
        logger.info(f"Public read policy applied to: {bucket_name}")
    except S3Error as e:
        logger.error(f"Error setting policy for {bucket_name}: {e}")

def init_minio_bucket(bucket_name: str) -> None:
    """
    Ensures the default buckets exists.
    """
    try:
        if not minio_client.bucket_exists(bucket_name):
            minio_client.make_bucket(bucket_name)
            logger.info(f"Created bucket: {bucket_name}")
        else:
            logger.info(f"Bucket already exists: {bucket_name}")
    except S3Error as e:
        logger.error(f"Error checking/creating bucket {bucket_name}: {e}")

def build_public_url(bucket_name: str, object_name: str) -> str:
    """
    Constructs a public HTTP URL for an object.

    Args:
        bucket_name (str): The S3 bucket name.
        object_name (str): The key/path of the object.

    Returns:
        str: The full URL string.
    """
    encoded_key = quote(object_name, safe='/')
    return f"{PUBLIC_MINIO_SCHEME}://{PUBLIC_MINIO_ENDPOINT}/{bucket_name}/{encoded_key}"
