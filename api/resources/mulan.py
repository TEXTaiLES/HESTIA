import json
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone

import numpy as np
import rasterio
from flask import Response, request, send_file
from flask_restful import Resource
from minio.error import S3Error
from psycopg2.extras import Json, RealDictCursor
from rasterio.crs import CRS
from rasterio.errors import RasterioIOError
from rasterio.transform import Affine
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.utils import secure_filename

from services.database import get_db_connection
from services.storage import minio_client, MINIO_MULTISPECTRAL_BUCKET, MINIO_ANNOTATIONS_BUCKET
from services.utils import stream_object

logger = logging.getLogger(__name__)

COOKIE_NAME = os.environ.get('REFRESH_TOKEN_COOKIE_NAME', 'textailes_refresh_token')
HOST_DOMAIN = os.environ.get('HOST_DOMAIN', '').rstrip('/')
MULAN_ORIGINS = [o.strip() for o in os.environ.get('MULAN_ORIGINS', '').split(',') if o.strip()]
ALLOWED_ORIGINS = MULAN_ORIGINS + ([HOST_DOMAIN] if HOST_DOMAIN else [])
MAX_UPLOAD_BYTES = int(os.environ.get('MULAN_MAX_UPLOAD_BYTES', 512 * 1024 * 1024))
MAX_PACKAGE_EXPANDED_BYTES = int(os.environ.get('MULAN_MAX_PACKAGE_EXPANDED_BYTES', 1024 * 1024 * 1024))

IMAGES_URL = '/api/v1/multispectral/images'
ANNOTATION_FORMAT = 'mulan-semantic-segmentation'
ANNOTATION_VERSION = '1.0'
IMAGE_DTYPES = {'uint8', 'int8', 'uint16', 'int16', 'uint32', 'int32', 'float32', 'float64'}
MASK_DTYPES = {'uint8', 'uint16', 'int16', 'uint32', 'int32'}

CAPABILITIES = {
    ('multispectral_images', 'read'): 'images_read',
    ('multispectral_images', 'create'): 'images_create',
    ('annotations', 'read'): 'annotations_read',
    ('annotations', 'create'): 'annotations_create',
    ('annotations', 'update'): 'annotations_update',
}

SESSION_SQL = """
    SELECT u.id, u.email, u.role, r.admin_access,
           COALESCE(NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.last_name)), ''), u.email) AS display_name
    FROM directus_sessions s
    JOIN directus_users u ON u.id = s."user"
    LEFT JOIN directus_roles r ON r.id = u.role
    WHERE s.token = %s AND s.expires > now() AND u.status = 'active'
"""
PERMISSIONS_SQL = """
    SELECT collection, action FROM directus_permissions
    WHERE role = %s AND (collection, action) IN %s
"""

IMAGE_SELECT = """
    SELECT i.image_id, i.filename, i.width, i.height, i.channel_count, i.dtype, i.channel_names,
           i.georeferenced, i.size_bytes, i.created_at, i.created_by,
           to_char(i.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at,
           NULLIF(TRIM(CONCAT_WS(' ', u.first_name, u.last_name)), '') AS created_by_name,
           a.scene_id AS annotation_id,
           to_char(a.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS annotation_updated_at
    FROM multispectral_images i
    LEFT JOIN annotations a ON a.multispectral_image_id = i.image_id AND a.kind = 'multispectral-mask'
    LEFT JOIN directus_users u ON u.id = i.created_by
"""

ANNOTATED_IMAGE_SQL = """
    SELECT i.image_id, i.width, i.height, i.crs, i.transform,
           a.scene_id AS annotation_id, a.revision, a.annotation_metadata, a.mask_object_key, a.mask_dtype,
           to_char(a.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS annotation_created_at,
           to_char(a.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS annotation_updated_at
    FROM multispectral_images i
    LEFT JOIN annotations a ON a.multispectral_image_id = i.image_id AND a.kind = 'multispectral-mask'
    WHERE i.image_id = %s
"""


def api_error(status, code, message, details=None):
    return {'error': {'code': code, 'message': message, 'details': details or {}}}, status


def _image_to_json(row):
    image_id = str(row['image_id'])
    return {
        'image_id': image_id,
        'filename': row['filename'],
        'width': row['width'],
        'height': row['height'],
        'channel_count': row['channel_count'],
        'dtype': row['dtype'],
        'channel_names': row['channel_names'],
        'georeferenced': row['georeferenced'],
        'size_bytes': row['size_bytes'],
        'has_annotation': row['annotation_id'] is not None,
        'annotation_updated_at': row['annotation_updated_at'],
        'created_at': row['created_at'],
        'created_by': (
            {'id': str(row['created_by']), 'display_name': row['created_by_name']}
            if row['created_by'] else None
        ),
        'file_url': f'{IMAGES_URL}/{image_id}/file',
    }


class MulanResource(Resource):
    # Capabilities needed per HTTP method; read by dispatch_request so handlers stay plain.
    required_caps = {}
    allow_anonymous = False

    # Runs before every request handler.
    def dispatch_request(self, *args, **kwargs):
        request.max_content_length = MAX_UPLOAD_BYTES

        if 'image_id' in kwargs:
            try:
                kwargs['image_id'] = str(uuid.UUID(kwargs['image_id']))
            except ValueError:
                return api_error(400, 'invalid_image_id', 'The image id must be a UUID.')

        if request.method in ('POST','PUT') and request.headers.get('Origin') not in (None, *ALLOWED_ORIGINS):
            return api_error(403, 'origin_not_allowed', 'Cross-site request rejected.')

        try:
            self.session = None
            token = request.cookies.get(COOKIE_NAME)
            if token:
                # NOTE: closing() function can be used to other resources as well,
                #       and ditch the try-except stmt to manually close each connection.
                # NOTE: RealDictCursor should be used in other resources as well,
                #       as it helps data retrieval.
                with closing(get_db_connection()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(SESSION_SQL, (token,))
                    user = cur.fetchone()
                    if user is not None:
                        if user['admin_access']:
                            granted = set(CAPABILITIES.values())
                        else:
                            cur.execute(PERMISSIONS_SQL, (user['role'], tuple(CAPABILITIES)))
                            granted = {CAPABILITIES[(r['collection'], r['action'])] for r in cur.fetchall()}
                        self.session = {
                            'user': {
                                'id': str(user['id']),
                                'email': user['email'],
                                'display_name': user['display_name'],
                            },
                            'capabilities': {cap: cap in granted for cap in CAPABILITIES.values()},
                        }

            if not self.allow_anonymous:
                if self.session is None:
                    return api_error(401, 'unauthenticated', 'A HESTIA login is required.')

                method = 'get' if request.method == 'HEAD' else request.method.lower()
                caps = self.required_caps.get(method)
                if caps is None or not all(self.session['capabilities'][c] for c in caps):
                    return api_error(403, 'forbidden', 'You do not have permission for this action.')

            return super().dispatch_request(*args, **kwargs)
        except RequestEntityTooLarge:
            return api_error(413, 'payload_too_large', f'Uploads are limited to {MAX_UPLOAD_BYTES} bytes.')
        except HTTPException:
            raise
        except Exception:
            logger.exception('MulAn request %s %s failed', request.method, request.path)
            return api_error(500, 'internal_error', 'Internal server error.')


class MulanSessionResource(MulanResource):
    allow_anonymous = True

    def get(self):
        headers = {'Cache-Control': 'no-store', 'Vary': 'Cookie'}
        if self.session is None:
            return {
                'authenticated': False,
                'user': None,
                'capabilities': {cap: False for cap in CAPABILITIES.values()},
                'login_url': f'{HOST_DOMAIN}/archive/user/login',
                'egi_login_url': f'{HOST_DOMAIN}/archive/user/egi-login',
            }, 200, headers

        return {
            'authenticated': True,
            'user': self.session['user'],
            'capabilities': self.session['capabilities'],
            'logout_url': f'{HOST_DOMAIN}/archive/user/logout',
        }, 200, headers


class MsImageListResource(MulanResource):
    required_caps = {'get': ('images_read',), 'post': ('images_create',)}
    sort_orders = {'created_at': 'i.created_at DESC', 'filename': 'i.filename ASC'}

    def get(self):
        try:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 50))
        except ValueError:
            return api_error(400, 'invalid_pagination', 'page and per_page must be integers.')
        if page < 1 or per_page < 1:
            return api_error(400, 'invalid_pagination', 'page and per_page must be positive.')
        per_page = min(per_page, 100)

        sort = request.args.get('sort', 'created_at')
        if sort not in self.sort_orders:
            return api_error(400, 'invalid_sort', 'sort must be created_at or filename.')

        conditions, params = [], []
        if q := request.args.get('q'):
            escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            conditions.append('i.filename ILIKE %s')
            params.append(f'%{escaped}%')
        has_annotation = request.args.get('has_annotation')
        if has_annotation is not None:
            if has_annotation not in ('true', 'false'):
                return api_error(400, 'invalid_boolean', 'has_annotation must be true or false.')
            conditions.append('a.scene_id IS NOT NULL' if has_annotation == 'true' else 'a.scene_id IS NULL')
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ''

        with closing(get_db_connection()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f'SELECT count(*) AS total FROM ({IMAGE_SELECT}{where}) matches', params)
            total = cur.fetchone()['total']
            cur.execute(
                f'{IMAGE_SELECT}{where} ORDER BY {self.sort_orders[sort]}, i.image_id LIMIT %s OFFSET %s',
                (*params, per_page, (page - 1) * per_page),
            )
            rows = cur.fetchall()

        return {'items': [_image_to_json(r) for r in rows], 'page': page, 'per_page': per_page, 'total': total}, 200

    def post(self):
        upload = request.files.get('file')
        if upload is None or not upload.filename:
            return api_error(400, 'missing_file', 'A "file" field containing a TIFF is required.')
        if not upload.filename.lower().endswith(('.tif', '.tiff')):
            return api_error(415, 'unsupported_media_type', 'Only .tif and .tiff files are accepted.')
        filename = secure_filename(request.form.get('filename') or upload.filename) or 'image.tif'

        workdir = tempfile.mkdtemp(prefix='mulan-')
        try:
            path = os.path.join(workdir, 'source.tif')
            upload.save(path)

            try:
                with rasterio.open(path) as ds:
                    dtypes = set(ds.dtypes)
                    if ds.driver != 'GTiff' or ds.count < 1 or not dtypes <= IMAGE_DTYPES:
                        return api_error(422, 'invalid_tiff', 'The file is not a supported multiband TIFF.')
                    width, height, channel_count = ds.width, ds.height, ds.count
                    dtype = next(iter(dtypes)) if len(dtypes) == 1 else 'mixed'
                    channel_names = list(ds.descriptions)
                    crs = ds.crs.to_wkt() if ds.crs else None
                    transform = list(ds.transform)[:6] if ds.crs else None
            except RasterioIOError:
                return api_error(422, 'invalid_tiff', 'The file is not a readable TIFF.')

            image_id = str(uuid.uuid4())
            object_key = f'{image_id}/source.tif'
            try:
                minio_client.fput_object(MINIO_MULTISPECTRAL_BUCKET, object_key, path, content_type='image/tiff')
                with closing(get_db_connection()) as conn:
                    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                        cur.execute("""
                            INSERT INTO multispectral_images
                                (image_id, filename, object_key, content_type, size_bytes, width, height,
                                channel_count, dtype, channel_names, georeferenced, crs, transform, created_by)
                            VALUES (%s, %s, %s, 'image/tiff', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            image_id, filename, object_key, os.path.getsize(path), width, height,
                            channel_count, dtype, Json(channel_names), crs is not None, crs,
                            Json(transform) if transform else None, self.session['user']['id'],
                        ))
                        cur.execute(f'{IMAGE_SELECT} WHERE i.image_id = %s', (image_id,))
                        row = cur.fetchone()
            except Exception:
                logger.exception('Storing multispectral image %s failed', image_id)
                try:
                    minio_client.remove_object(MINIO_MULTISPECTRAL_BUCKET, object_key)
                except S3Error:
                    logger.warning('Could not remove orphaned object %s', object_key)
                return api_error(500, 'storage_error', 'The image could not be stored.')
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        return _image_to_json(row), 201, {'Location': f'{IMAGES_URL}/{image_id}'}


class MsImageResource(MulanResource):
    required_caps = {'get': ('images_read',)}

    def get(self, image_id):
        with closing(get_db_connection()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f'{IMAGE_SELECT} WHERE i.image_id = %s', (image_id,))
            row = cur.fetchone()
        if row is None:
            return api_error(404, 'image_not_found', 'No such multispectral image.')
        return _image_to_json(row), 200


class MsImageFileResource(MulanResource):
    required_caps = {'get': ('images_read',)}

    def get(self, image_id):
        with closing(get_db_connection()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT filename, object_key FROM multispectral_images WHERE image_id = %s', (image_id,))
            row = cur.fetchone()
        if row is None:
            return api_error(404, 'image_not_found', 'No such multispectral image.')

        stat = minio_client.stat_object(MINIO_MULTISPECTRAL_BUCKET, row['object_key'])
        headers = {'ETag': f'"{stat.etag}"', 'Cache-Control': 'private'}
        if request.if_none_match.contains(stat.etag):
            return Response(status=304, headers=headers)

        response = stream_object(
            MINIO_MULTISPECTRAL_BUCKET, row['object_key'],
            download_name=row['filename'], mimetype='image/tiff',
        )
        response.headers.update(headers)
        response.headers['Content-Length'] = str(stat.size)
        return response


class MsAnnotationResource(MulanResource):
    required_caps = {
        'get': ('images_read', 'annotations_read'),
        'put': ('images_read',),
    }

    def get(self, image_id):
        with closing(get_db_connection()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(ANNOTATED_IMAGE_SQL, (image_id,))
            row = cur.fetchone()
        if row is None:
            return api_error(404, 'image_not_found', 'No such multispectral image.')
        if row['annotation_id'] is None:
            return api_error(404, 'annotation_not_found', 'This image has no annotation.')

        return {
            'annotation_id': row['annotation_id'],
            'multispectral_image_id': image_id,
            'format': ANNOTATION_FORMAT,
            'format_version': ANNOTATION_VERSION,
            'revision': row['revision'],
            'classes': row['annotation_metadata']['classes'],
            'mask': {
                'width': row['width'],
                'height': row['height'],
                'dtype': row['mask_dtype'],
                'background_id': 0,
                'ignore_id': None,
            },
            'created_at': row['annotation_created_at'],
            'updated_at': row['annotation_updated_at'],
            'package_url': f'{IMAGES_URL}/{image_id}/annotation/package',
        }, 200

    def put(self, image_id):
        package = request.files.get('package')
        if package is None or not package.filename:
            return api_error(400, 'missing_package', 'A "package" field containing a ZIP is required.')
        overwrite = request.form.get('overwrite', 'false').lower()
        if overwrite not in ('true', 'false'):
            return api_error(400, 'invalid_boolean', 'overwrite must be true or false.')
        overwrite = overwrite == 'true'
        expected_revision = request.form.get('expected_revision')
        if expected_revision is not None:
            try:
                expected_revision = int(expected_revision)
            except ValueError:
                return api_error(400, 'invalid_expected_revision', 'expected_revision must be an integer.')

        caps = self.session['capabilities']
        if not (caps['annotations_create'] or caps['annotations_update']):
            return api_error(403, 'forbidden', 'You do not have permission for this action.')

        with closing(get_db_connection()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(ANNOTATED_IMAGE_SQL, (image_id,))
            image = cur.fetchone()

        if image is None:
            return api_error(404, 'image_not_found', 'No such multispectral image.')

        workdir = tempfile.mkdtemp(prefix='mulan-')
        mask_key = None
        stored = False
        try:
            package_path = os.path.join(workdir, 'package.zip')
            mask_path = os.path.join(workdir, 'mask.tif')
            package.save(package_path)
            if not zipfile.is_zipfile(package_path):
                return api_error(415, 'unsupported_media_type', 'The package must be a ZIP file.')
            try:
                with zipfile.ZipFile(package_path) as archive:
                    entries = archive.infolist()
                    if sorted(e.filename for e in entries) != ['annotations.json', 'mask.tif']:
                        return api_error(422, 'invalid_annotation_package',
                                         'The ZIP must contain exactly maks.tif and annotations.json at its root.')
                    if sum(e.file_size for e in entries) > MAX_PACKAGE_EXPANDED_BYTES:
                        return api_error(413, 'payload_too_large', 'The unpacked package is too large.')
                    with archive.open('mask.tif') as src, open(mask_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                    metadata = json.loads(archive.read('annotations.json'))
            except (zipfile.BadZipFile, ValueError):
                return api_error(422, 'invalid_annotation_package', 'The package is corrupt or annotations.json is not JSON.')

            try:
                with rasterio.open(mask_path) as mask:
                    if mask.count != 1 or mask.dtypes[0] not in MASK_DTYPES:
                        return api_error(422, 'invalid_annotation_package', 'mask.tif must be a single-band integer TIFF.')
                    if (mask.width, mask.height) != (image['width'], image['height']):
                        return api_error(422, 'mask_dimension_mismatch',
                                         'mask.tif dimensions differ from the image.',
                                         {'expected': [image['width'], image['height']],
                                          'actual': [mask.width, mask.height]})

                    if image['crs'] and (
                        mask.crs is None
                        or mask.crs != CRS.from_wkt(image['crs'])
                        or not mask.transform.almost_equals(Affine(*image['transform']))
                    ):
                        return api_error(422, 'metadata_conflict', 'mask.tif CRS or transform differs from the image.')

                    mask_dtype = mask.dtypes[0]
                    used_ids = set()
                    for _, window in mask.block_windows(1):
                        used_ids.update(np.unique(mask.read(1, window=window)).tolist())
            except RasterioIOError:
                return api_error(422, 'invalid_annotation_package', 'mask.tif is not a readable TIFF.')

            try:
                class_ids = [c['id'] for c in metadata['classes']]
                valid = (
                    metadata['format'] == ANNOTATION_FORMAT
                    and metadata['version'] == ANNOTATION_VERSION
                    and all(isinstance(i, int) and 0 <= i <= 65535 for i in class_ids)
                    and len(set(class_ids)) == len(class_ids)
                    and all(isinstance(c['name'], str) for c in metadata['classes'])
                    and any(c['id'] == 0 and c['name'] == 'Background' for c in metadata['classes'])
                )
            except (KeyError, TypeError):
                valid = False
            if not valid:
                return api_error(422, 'invalid_annotation_package', 'annotations.json is not a valid MulAn v1 file.')
            declared_image = metadata.get('image') or {}
            declared_mask = metadata.get('mask') or {}
            if (
                declared_image.get('width', image['width']) != image['width']
                or declared_image.get('height', image['height']) != image['height']
                or declared_mask.get('background_id', 0) != 0
                or declared_mask.get('ignore_id') is not None
            ):
                return api_error(422, 'metadata_conflict', 'annotations.json disagrees with the image or mask.')
            if not used_ids <= set(class_ids):
                return api_error(422, 'metadata_conflict', 'mask.tif uses class ids with no class definition.',
                                 {'undefined_ids': sorted(used_ids - set(class_ids))})

            mask_key = f'{image_id}/mask-{uuid.uuid4()}.tif'
            minio_client.fput_object(MINIO_ANNOTATIONS_BUCKET, mask_key, mask_path, content_type='image/tiff')
            now = datetime.now(timezone.utc)
            user_id = self.session['user']['id']

            with closing(get_db_connection()) as conn:
                with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(f'{ANNOTATED_IMAGE_SQL} FOR UPDATE OF i', (image_id,))
                    current = cur.fetchone()
                    exists = current['annotation_id'] is not None

                    if not caps['annotations_update' if exists else 'annotations_create']:
                        return api_error(403, 'forbidden', 'You do not have permission for this action.')
                    if exists:
                        details = {
                            'annotation_id': current['annotation_id'],
                            'revision': current['revision'],
                            'updated_at': current['annotation_updated_at'],
                        }
                        if not overwrite:
                            return api_error(409, 'annotation_exists', 'This image already has an annotation.', details)
                        if expected_revision is not None and expected_revision != current['revision']:
                            return api_error(409, 'annotation_revision_conflict',
                                             'The annotation changed since you last loaded it.', details)

                        cur.execute("""
                            UPDATE annotations
                            SET mask_object_key = %s, mask_dtype = %s, annotation_metadata = %s,
                                format_version = %s, revision = revision + 1,
                                updated_by = %s, updated_at = %s, "timestamp" = %s
                            WHERE scene_id = %s
                        """, (mask_key, mask_dtype, Json(metadata), ANNOTATION_VERSION,
                              user_id, now, now.isoformat(), current['annotation_id']))

                    else:
                        cur.execute("""
                            INSERT INTO annotations
                                (scene_id, collaborative, "timestamp", kind, multispectral_image_id,
                                mask_object_key, mask_dtype, annotation_metadata, format_version,
                                revision, created_by, updated_by, created_at, updated_at)
                            VALUES (%s, false, %s, 'multispectral-mask', %s, %s, %s, %s, %s, 1, %s, %s, %s, %s)
                        """, (str(uuid.uuid4()), now.isoformat(), image_id, mask_key, mask_dtype,
                              Json(metadata), ANNOTATION_VERSION, user_id, user_id, now, now))

            stored = True

            if exists:
                try:
                    minio_client.remove_object(MINIO_ANNOTATIONS_BUCKET, current['mask_object_key'])
                except S3Error:
                    logger.warning('Could not remove replaced mask %s', current['mask_object_key'])
        finally:
            if mask_key and not stored:
                try:
                    minio_client.remove_object(MINIO_ANNOTATIONS_BUCKET, mask_key)
                except S3Error:
                    logger.warning('Could not remove unused mask %s', mask_key)
            shutil.rmtree(workdir, ignore_errors=True)

        body, _ = self.get(image_id)
        return body, 200 if exists else 201


class MsAnnotationPackageResource(MulanResource):
    required_caps = {'get': ('images_read', 'annotations_read')}

    def get(self, image_id):
        with closing(get_db_connection()) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(ANNOTATED_IMAGE_SQL, (image_id,))
            row = cur.fetchone()
        if row is None:
            return api_error(404, 'image_not_found', 'No such multispectral image.')
        if row['annotation_id'] is None:
            return api_error(404, 'annotation_not_found', 'This image has no annotation.')

        buffer = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
        mask = minio_client.get_object(MINIO_ANNOTATIONS_BUCKET, row['mask_object_key'])
        try:
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                with archive.open('mask.tif', 'w') as dst:
                    shutil.copyfileobj(mask, dst, 1024 * 1024)
                archive.writestr('annotations.json', json.dumps(row['annotation_metadata'], indent=2))
        finally:
            mask.close()
            mask.release_conn()
        buffer.seek(0)
        return send_file(buffer, mimetype='application/zip', as_attachment=True,
                         download_name='annotation-mask.zip')
