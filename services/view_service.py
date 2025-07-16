from typing import List
from fastapi import HTTPException
import psycopg2.extras
from core.db import get_connection, release_connection
from models.view_models import ViewSaveRequest, ViewResponse
import json


class ViewService:
    def save_user_view(self, view_request: ViewSaveRequest) -> ViewResponse:
        conn = get_connection()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute("""
                INSERT INTO operations.user_views (user_id, category_id, view_name, view_payload)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, category_id, view_name)
                DO UPDATE SET
                    view_payload = EXCLUDED.view_payload,
                    updated_at = NOW()
                RETURNING id, user_id, category_id, view_name, view_payload
            """, (
                view_request.user_id,
                view_request.category_id,
                view_request.view_name,
                json.dumps(view_request.view_payload)  # <-- convert dict to JSON string
            ))
            row = cur.fetchone()
            conn.commit()
            return ViewResponse(**row)

        except Exception as e:
            conn.rollback()
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            release_connection(conn)

    def get_user_views(self, user_id: str) -> List[ViewResponse]:
      conn = get_connection()
      try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
              SELECT id, user_id, category_id, view_name, view_payload
              FROM operations.user_views
              WHERE user_id = %s
          """, (user_id,))
        rows = cur.fetchall()
        result = []
        for row in rows:
          data = dict(row)
          # If view_payload is a string, load it as JSON
          if isinstance(data['view_payload'], str):
            try:
              data['view_payload'] = json.loads(data['view_payload'])
            except Exception:
              # fallback, just leave as string if not valid JSON
              pass
          result.append(ViewResponse(**data))
        return result
      finally:
        release_connection(conn)
