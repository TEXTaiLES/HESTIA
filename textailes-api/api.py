from flask import Flask, send_from_directory, jsonify
from flask_restful import Api
from flasgger import Swagger
from datetime import datetime, timezone

# Resources
from resources.artifact import ArtifactResource, ArtifactItemResource
from resources.sensor import SensorReadingResource
from resources.robot import RobotImageResource

app = Flask(__name__)

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

@app.route('/health')
def health_check():
    """Return API health status with current timestamp."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
