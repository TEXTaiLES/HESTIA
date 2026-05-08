from flask import send_file
from flask_restful import Resource
import io
import logging

from middleware.security import require_api_key
from services.storage import minio_client

logger = logging.getLogger(__name__)


class FileProxyResource(Resource):
    method_decorators = [require_api_key]

    def get(self, bucket_name: str, object_name: str):
        """
        Securely retrieve and stream an object from MinIO storage.

        Path Params:
            bucket_name (str): The destination MinIO bucket.
            object_name (str): The object key/path inside the bucket.
        """
        try:
            # Fetch the object from internal MinIO
            response = minio_client.get_object(bucket_name, object_name)
            file_data = response.read()

            # Determine basic mimetype for proper browser/client rendering
            mimetype = 'application/octet-stream'
            lower_name = object_name.lower()

            if lower_name.endswith(('.jpg', '.jpeg')):
                mimetype = 'image/jpeg'
            elif lower_name.endswith('.png'):
                mimetype = 'image/png'
            elif lower_name.endswith('.json'):
                mimetype = 'application/json'
            elif lower_name.endswith('.txt'):
                mimetype = 'text/plain'
            elif lower_name.endswith('.glb'):
                mimetype = 'model/gltf-binary'
            elif lower_name.endswith('.gltf'):
                mimetype = 'model/gltf+json'
            elif lower_name.endswith('.mtl'):
                mimetype = 'model/mtl'
            elif lower_name.endswith('.obj'):
                mimetype = 'model/obj'
            # add more MIME types as needed (https://mime-type.com/mime-types)

            # Stream the file back to the requester
            return send_file(
                io.BytesIO(file_data),
                mimetype=mimetype,
                as_attachment=False,
                download_name=object_name.split('/')[-1]
            )

        except Exception as e:
            logger.error(f"Error serving file '{object_name}' from bucket '{bucket_name}': {e}")
            return {'error': 'File not found or access denied.'}, 404

        finally:
            # Ensure the MinIO HTTP connection is released back to the pool
            if 'response' in locals():
                response.close()
                response.release_conn()