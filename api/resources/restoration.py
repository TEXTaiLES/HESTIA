from flask import request, jsonify
from flask_restful import Resource
from datetime import datetime, timezone
from psycopg2.extras import Json
import uuid
import logging

from middleware.security import require_api_key
from services.database import get_db_connection

logger = logging.getLogger(__name__)

ARTEFACT_FILTERABLE_FIELDS = {'restoration_artefact_id', 'root_artefact_id', 'created_by'}


def _rows_to_dicts(cur, rows):
    cols = [d[0] for d in cur.description]
    results = []
    for row in rows:
        d = {}
        for col, val in zip(cols, row):
            d[col] = val.isoformat() if isinstance(val, datetime) else val
        results.append(d)
    return results


def _nest_image(row, prefix):
    """Collapse flattened {prefix}_asset/width/height/mime_type columns into a nested dict (or None)."""
    asset = row.get(f'{prefix}_asset')
    if not asset:
        return None
    return {
        'asset': asset,
        'width': row.get(f'{prefix}_width'),
        'height': row.get(f'{prefix}_height'),
        'mime_type': row.get(f'{prefix}_mime_type'),
    }


def _strip_flat_image_cols(row):
    for prefix in ('primary_image', 'thumbnail_image'):
        for suffix in ('asset', 'width', 'height', 'mime_type'):
            row.pop(f'{prefix}_{suffix}', None)
    return row


class RestorationResource(Resource):
    method_decorators = [require_api_key]

    def get(self):
        """
        List restoration artefacts with their nested media and results.

        Query params:
          restoration_artefact_id, root_artefact_id, created_by — exact-match filters
          page (default 1), per_page (default 50)
        """
        conn = None
        try:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 50))
            offset = (page - 1) * per_page

            sql = "SELECT * FROM restoration_artefacts WHERE 1=1"
            params = []
            for key in ARTEFACT_FILTERABLE_FIELDS:
                value = request.args.get(key)
                if value:
                    sql += f" AND {key} = %s"
                    params.append(value)
            sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
            params.extend([per_page, offset])

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            artefact_rows = _rows_to_dicts(cur, cur.fetchall())

            if not artefact_rows:
                cur.close()
                conn.close()
                return jsonify([])

            artefact_ids = [r['restoration_artefact_id'] for r in artefact_rows]

            # Fetch media for all artefacts in one query
            cur.execute(
                "SELECT * FROM restoration_media WHERE restoration_artefact_id = ANY(%s::uuid[]) ORDER BY created_at ASC",
                (artefact_ids,)
            )
            media_rows = _rows_to_dicts(cur, cur.fetchall())

            # Fetch results for all artefacts in one query
            cur.execute(
                "SELECT * FROM restoration_results WHERE restoration_artefact_id = ANY(%s::uuid[]) ORDER BY created_at ASC",
                (artefact_ids,)
            )
            result_rows = _rows_to_dicts(cur, cur.fetchall())

            cur.close()
            conn.close()

            # Group media and results by artefact_id

            # Pre-init dicts to ensure all artefacts are represented, even those without media/results
            media_by_artefact = {aid: [] for aid in artefact_ids}
            for m in media_rows:
                aid = m.pop('restoration_artefact_id')
                media_by_artefact.setdefault(aid, []).append(m)

            results_by_artefact = {aid: [] for aid in artefact_ids}
            for r in result_rows:
                aid = r.pop('restoration_artefact_id')
                # Convert input_media_ids UUIDs to strings for JSON
                if r.get('input_media_ids'):
                    r['input_media_ids'] = [str(x) for x in r['input_media_ids']]
                results_by_artefact.setdefault(aid, []).append(r)

            output = []
            for row in artefact_rows:
                aid = row['restoration_artefact_id']
                row['primary_image'] = _nest_image(row, 'primary_image')
                row['thumbnail_image'] = _nest_image(row, 'thumbnail_image')
                _strip_flat_image_cols(row)
                row['associated_media'] = media_by_artefact.get(aid, [])
                row['restoration_results'] = results_by_artefact.get(aid, [])
                output.append(row)

            return jsonify(output)

        except Exception as e:
            logger.error(f"Error fetching restoration artefacts: {e}")
            if conn:
                conn.close()
            return {'error': str(e)}, 500

    def post(self):
        """
        Create a restoration artefact with its associated media and results.

        Body (JSON):
          title (required)
          description, created_by, root_artefact_id
          primary_image (required): {asset, width, height, mime_type}
          thumbnail_image (optional): {asset, width, height, mime_type}
          associated_media (optional): [{media_id?, title, description, purpose,
                                          asset, mime_type, width, height, source, created_by}]
          restoration_results (optional): [{result_id?, asset, model, run_id,
                                             candidate_index, input_media_ids, parameters, created_by}]

          IDs (restoration_artefact_id, media_id, result_id) are server-generated if omitted.
          Client may provide media_id values up-front so results can reference them.
        """
        body = request.get_json(silent=True)
        if not body:
            return {'error': "No data provided"}, 400

        title = body.get('title')
        if not title:
            return {'error': "Missing required field 'title'"}, 400

        primary_image = body.get('primary_image') or {}
        if not primary_image.get('asset'):
            return {'error': "Missing required field 'primary_image.asset'"}, 400

        thumbnail_image = body.get('thumbnail_image') or {}

        restoration_artefact_id = body.get('restoration_artefact_id') or str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # Pre-assign UUIDs to associated_media so results can reference them
        media_in = body.get('associated_media') or []
        for m in media_in:
            if not m.get('media_id'):
                m['media_id'] = str(uuid.uuid4())

        results_in = body.get('restoration_results') or []
        for r in results_in:
            if not r.get('result_id'):
                r['result_id'] = str(uuid.uuid4())

        try:
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO restoration_artefacts (
                        restoration_artefact_id, title, description, root_artefact_id,
                        primary_image_asset, primary_image_width, primary_image_height, primary_image_mime_type,
                        thumbnail_image_asset, thumbnail_image_width, thumbnail_image_height, thumbnail_image_mime_type,
                        created_at, created_by, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        restoration_artefact_id,
                        title,
                        body.get('description'),
                        body.get('root_artefact_id'),
                        primary_image.get('asset'),
                        primary_image.get('width'),
                        primary_image.get('height'),
                        primary_image.get('mime_type'),
                        thumbnail_image.get('asset'),
                        thumbnail_image.get('width'),
                        thumbnail_image.get('height'),
                        thumbnail_image.get('mime_type'),
                        now,
                        body.get('created_by'),
                        now,
                    )
                )

                for m in media_in:
                    cur.execute(
                        """
                        INSERT INTO restoration_media (
                            media_id, restoration_artefact_id, title, description, purpose,
                            asset, mime_type, width, height, source, created_at, created_by
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            m['media_id'],
                            restoration_artefact_id,
                            m.get('title'),
                            m.get('description'),
                            m.get('purpose'),
                            m.get('asset'),
                            m.get('mime_type'),
                            m.get('width'),
                            m.get('height'),
                            m.get('source'),
                            now,
                            m.get('created_by') or body.get('created_by'),
                        )
                    )

                for r in results_in:
                    model = r.get('model')
                    parameters = r.get('parameters')
                    cur.execute(
                        """
                        INSERT INTO restoration_results (
                            result_id, restoration_artefact_id, asset, model, run_id,
                            candidate_index, input_media_ids, parameters, created_at, created_by
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s::uuid[], %s, %s, %s)
                        """,
                        (
                            r['result_id'],
                            restoration_artefact_id,
                            r.get('asset'),
                            Json(model) if model is not None else None,
                            r.get('run_id'),
                            r.get('candidate_index'),
                            r.get('input_media_ids'),
                            Json(parameters) if parameters is not None else None,
                            now,
                            r.get('created_by') or body.get('created_by'),
                        )
                    )

            return {
                'message': "Restoration artefact created.",
                'restoration_artefact_id': restoration_artefact_id,
                'media_ids': [m['media_id'] for m in media_in],
                'result_ids': [r['result_id'] for r in results_in],
            }, 201

        except Exception as e:
            logger.error(f"Failed to create restoration artefact: {e}")
            return {'error': str(e)}, 500