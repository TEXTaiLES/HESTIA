from flask import Flask, send_from_directory, jsonify
from flask_restful import Api
from flask_cors import CORS
from flasgger import Swagger
from datetime import datetime, timezone
import logging

# Resources
from resources.artifact import ArtifactResource, ArtifactItemResource
from resources.sensor import SensorReadingResource
from resources.robot import RobotImageResource
from resources.reconstruction import ReconstructionResource
from resources.annotation import AnnotationResource
from resources.file_proxy import FileProxyResource
from resources.thumbnail import ThumbnailResource

# Setup
from scripts.setup_infrastructure import setup_minio, run_migrations, wait_for_postgres

app = Flask(__name__)
CORS(app)

# Swagger Configuration
@app.route('/swagger.json')
def swagger_spec():
    return send_from_directory('static', 'swagger.json')

Swagger(app, config={
    'specs': [
        {'endpoint': 'swagger', 'route': '/swagger.json',
         'rule_filter': lambda rule: True, 'model_filter': lambda tag: True}
    ],
    'static_url_path': '/flasgger_static',
    'swagger_ui': True,
    'specs_route': '/docs',
    'headers': []
})

api = Api(app)

# Route Registration
api.add_resource(ArtifactResource, '/artifacts')
api.add_resource(ArtifactItemResource, '/artifacts/<string:artifact_id>')
api.add_resource(SensorReadingResource, '/sensor-readings')
api.add_resource(RobotImageResource, '/robot-images')
api.add_resource(ReconstructionResource, '/reconstructions')
api.add_resource(AnnotationResource, '/annotations')
api.add_resource(FileProxyResource, '/storage/<string:bucket_name>/<path:object_name>')
api.add_resource(ThumbnailResource, '/reconstructions/<string:object_id>/thumbnail')

@app.route('/health')
def health_check():
    """Return API health status with current timestamp."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

if __name__ == '__main__':
    print("--- Starting Infrastructure Setup ---")
    if wait_for_postgres():
        run_migrations()
        setup_minio()
        print("--- Setup Complete. Starting API ---")
    else:
        print("--- Database not ready. Starting API anyway (might fail) ---")

    app.run(debug=True, host='0.0.0.0', port=5000)
