from flask import Flask, send_from_directory, jsonify
from flask_restful import Api
from flask_cors import CORS
from flasgger import Swagger
from datetime import datetime, timezone
import logging

# Resources
from resources.artifact import ArtifactResource, ArtifactItemResource, ArtifactAggregateResource
from resources.sensor import SensorResource, SensorReadingResource
from resources.robot import RobotImageResource
from resources.reconstruction import ReconstructionResource
from resources.annotation import AnnotationResource
from resources.artefact_metadata import ArtefactMetadataResource
from resources.echoes import EchoesResource
from resources.file_proxy import FileProxyResource
from resources.scene import SceneResource
from resources.thumbnail import ThumbnailResource
from resources.artefact_digital_twin import ArtefactDigitalTwinUriResource
from resources.nefele_job import NefeleResource, NefeleJobResource, NefeleClaimResource, NefelePreviewResource, NefeleCancelResource
from resources.amalthai_dataset import AmalthaiDatasetResource, AmalthaiDatasetItemResource, AmalthaiDatasetArchiveResource
from resources.amalthai_model import AmalthaiModelResource, AmalthaiModelItemResource, AmalthaiModelWeightsResource, AmalthaiModelConfigResource
from resources.amalthai_experiment import AmalthaiExperimentResource, AmalthaiExperimentItemResource
from resources.amalthai_inference import AmalthaiInferenceResource, AmalthaiInferenceItemResource, AmalthaiInferenceInputsResource, AmalthaiInferenceOutputsResource
from resources.multispectral import (
    MultispectralImageFileResource,
    MultispectralImageListResource,
    MultispectralImageResource,
)
from resources.rgb import RgbImageFileResource, RgbImageListResource, RgbImageResource

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
api.add_resource(SensorResource, '/sensors')
api.add_resource(ArtifactAggregateResource, '/artefacts/<string:artifact_id>')
api.add_resource(SensorReadingResource, '/sensor-readings')
api.add_resource(RobotImageResource, '/robot-images')
api.add_resource(ReconstructionResource, '/reconstructions')
api.add_resource(AnnotationResource, '/annotations')
api.add_resource(EchoesResource, '/echoes/<string:artifact_id>')
api.add_resource(FileProxyResource, '/storage/<string:bucket_name>/<path:object_name>')
api.add_resource(ThumbnailResource, '/reconstructions/<string:object_id>/generate-thumbnail')
api.add_resource(ArtefactDigitalTwinUriResource, '/artefacts/<string:artefact_id>/digital-twin-uri')
api.add_resource(NefeleResource, '/nefele')
api.add_resource(NefeleJobResource, '/nefele/<string:job_id>')
api.add_resource(NefeleClaimResource, '/nefele/claim')
api.add_resource(NefelePreviewResource, '/nefele/<string:job_id>/preview')
api.add_resource(NefeleCancelResource, '/nefele/<string:job_id>/cancel')
api.add_resource(AmalthaiDatasetResource, '/amalthai/datasets')
api.add_resource(AmalthaiDatasetItemResource, '/amalthai/datasets/<string:dataset_id>')
api.add_resource(AmalthaiDatasetArchiveResource, '/amalthai/datasets/<string:dataset_id>/archive')
api.add_resource(AmalthaiModelResource, '/amalthai/models')
api.add_resource(AmalthaiModelItemResource, '/amalthai/models/<string:model_id>')
api.add_resource(AmalthaiModelWeightsResource, '/amalthai/models/<string:model_id>/weights')
api.add_resource(AmalthaiModelConfigResource, '/amalthai/models/<string:model_id>/config')
api.add_resource(AmalthaiExperimentResource, '/amalthai/experiments')
api.add_resource(AmalthaiExperimentItemResource, '/amalthai/experiments/<string:experiment_id>')
api.add_resource(AmalthaiInferenceResource, '/amalthai/inference-runs')
api.add_resource(AmalthaiInferenceItemResource, '/amalthai/inference-runs/<string:inference_id>')
api.add_resource(AmalthaiInferenceInputsResource, '/amalthai/inference-runs/<string:inference_id>/inputs')
api.add_resource(AmalthaiInferenceOutputsResource, '/amalthai/inference-runs/<string:inference_id>/outputs')
api.add_resource(RgbImageListResource, '/rgb/images')
api.add_resource(RgbImageResource, '/rgb/image', '/rgb/images/<string:image_name>')
api.add_resource(RgbImageFileResource, '/rgb/images/<string:image_name>/file')
api.add_resource(MultispectralImageListResource, '/multispectral/images')
api.add_resource(MultispectralImageResource, '/multispectral/image', '/multispectral/images/<path:image_name>')
api.add_resource(MultispectralImageFileResource, '/multispectral/file')
api.add_resource(SceneResource, '/scenes', '/scenes/<string:scene_id>')
api.add_resource(ArtefactMetadataResource, '/artefact_metadata', '/artefact_metadata/<string:artefact_id>')

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
