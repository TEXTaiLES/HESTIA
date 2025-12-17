from flask import request, jsonify
from flask_restful import Resource
from datetime import datetime, timezone
import logging

from services.thoth_utils import (
    create_scene_object,
    create_scene_name,
    get_name_from_url
)


logger = logging.getLogger(__name__)


THOTH_BASE_DOMAIN = "" #PORT/a/thoth/ 


class ThothSceneResource(Resource):

    def get_scene_url(self, object_url):
        """
        Gets/creates object scene and redirects to thoth
        """
        if not object_url:
            return {'error': 'Invalid URL'}
        
        # 1. Check if object is valid
        # NOTE just check if it exists, otherwise throw error 
        
        # 2. Check if scene with that object exists
        # NOTE For now, we only have one scene per object.
        # NOTE The scene with object A will have a name {A}_{s}
        # NOTE where s the datetime it was created on
        object_name = get_name_from_url(object_url)
        
        scene_name = "the scene with the object" or None
        # 2.1 If scene exists in datalake, redirect to thoth
        if (scene_name): 
            scene_url = THOTH_BASE_DOMAIN + "/?s=" + scene_name
            return scene_url
        # 2.2 If not, create scene, post it to the database and retry
        else:
            scene_name   = create_scene_name(object_url)
            scene_object = create_scene_object(object_url)
            
            # post scene_object to database
            self.get_scene_url(object_url)
    
        # NOTE IMPORTANT I have to update the base scene location url,
        # NOTE So tell me when that's setup
        # NOTE Msg me for clarification anytime