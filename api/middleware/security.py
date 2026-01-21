import os
from functools import wraps
from flask import request

# Configuration
MASTER_API_KEY = os.environ.get('API_SECRET_KEY')

def require_api_key(f):
    """
    Flask decorator to require API Key authentication header.

    Usage:
        @require_api_key
        def get(self): ...
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        provided_key = request.headers.get('Authorization')
        if MASTER_API_KEY and provided_key == f"Bearer {MASTER_API_KEY}":
            return f(*args, **kwargs)
        return {'error': 'Unauthorized'}, 401
    return decorated_function