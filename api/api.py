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
from resources.nefele_job import NefeleResource, NefeleJobResource, NefeleClaimResource, NefelePreviewResource
# TODO: restoration.py is not yet on this branch — restore from portal/improvement before re-enabling.
# from resources.restoration import RestorationResource
from resources.thread_simulation import ThreadSimulationResource, ThreadSimulationItemResource, ThreadSimulationVisualizationResource, ThreadSimulationDownloadResource
from resources.patch_simulation import PatchSimulationResource, PatchSimulationItemResource, PatchSimulationVisualizationResource, PatchSimulationDownloadResource

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
api.add_resource(ThumbnailResource, '/reconstructions/<string:object_id>/generate-thumbnail')
api.add_resource(NefeleResource, '/nefele')
api.add_resource(NefeleJobResource, '/nefele/<string:job_id>')
api.add_resource(NefeleClaimResource, '/nefele/claim')
api.add_resource(NefelePreviewResource, '/nefele/<string:job_id>/preview')
# api.add_resource(RestorationResource, '/restorations')
api.add_resource(ThreadSimulationResource, '/dynamo/thread-simulations')
api.add_resource(ThreadSimulationItemResource, '/dynamo/thread-simulations/<string:simulation_id>')
api.add_resource(ThreadSimulationVisualizationResource, '/dynamo/thread-simulations/<string:simulation_id>/visualization.glb')
api.add_resource(ThreadSimulationDownloadResource, '/dynamo/thread-simulations/<string:simulation_id>/download.zip')
api.add_resource(PatchSimulationResource, '/dynamo/patch-simulations')
api.add_resource(PatchSimulationItemResource, '/dynamo/patch-simulations/<string:simulation_id>')
api.add_resource(PatchSimulationVisualizationResource, '/dynamo/patch-simulations/<string:simulation_id>/visualization/<string:experiment>.glb')
api.add_resource(PatchSimulationDownloadResource, '/dynamo/patch-simulations/<string:simulation_id>/download.zip')

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
