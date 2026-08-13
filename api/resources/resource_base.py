from flask import request
from flask_restful import Resource
from typing import Any
from datetime import datetime
import logging

from middleware.security import require_api_key
from services.database import get_db_connection

logger = logging.getLogger(__name__)


class BadRequest(Exception):
    """Raised when the client's input is invalid."""


class ResourceBase(Resource):
    method_decorators = [require_api_key]
    required_attributes = ("table",)
    table: str
    avro_schema = ""
    order_by = ""
    per_page_default = 50
    per_page_max = 200

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for attr in cls.required_attributes:
            if not getattr(cls, attr, None):
                raise TypeError(f"{cls.__name__} must define a non-empty `{attr}`")

    def build_GET_fields(self) -> list[str]:
        return []  # empty list defaults to all fields ('*')

    def build_GET_conditions(self) -> list[tuple[str, str, Any]]:  # (col, oper, val)
        return []

    def get(self):
        conn = None
        try:
            try:
                page = max(1, int(request.args.get('page', 1)))
                per_page = min(
                    self.per_page_max,
                    max(1, int(request.args.get('per_page', self.per_page_default)))
                )
            except ValueError:
                return {'error': 'Page and per_page must be integers'}, 400

            offset = (page - 1) * per_page

            sql = f"SELECT {', '.join(self.build_GET_fields()) or '*'}"
            sql += f" FROM {self.table} WHERE 1=1"
            params = []
            for condition in self.build_GET_conditions():
                if condition[1] not in ['=', '!=', '<=', '>=', '<', '>']:
                    raise ValueError(f"'{condition[1]}' is not a valid SQL operator.")
                sql += f" AND {condition[0]} {condition[1]} %s"
                params.append(condition[2])

            if self.order_by:
                sql += f" ORDER BY {self.order_by}"

            sql += " LIMIT %s OFFSET %s;"
            params.extend([per_page, offset])

            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()

            results = []
            if cur.description:
                colnames = [desc[0] for desc in cur.description]
                for row in rows:
                    row_dict = {}
                    for col, val in zip(colnames, row):
                        if isinstance(val, datetime):
                            row_dict[col] = val.isoformat()
                        else:
                            row_dict[col] = val
                    results.append(row_dict)

            return results, 200
        except BadRequest as e:
            return {'error': str(e)}, 400
        except Exception as e:
            logger.error(f"Error fetching {self.table}: {e}")
            return {'error': 'Internal server error'}, 500
        finally:
            if conn is not None:
                conn.close()

    def build_POST_fields(self) -> list[str]:
        return []

    def post(self):
        raise NotImplementedError()
