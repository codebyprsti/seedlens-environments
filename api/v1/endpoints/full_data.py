import re
import psycopg2
import psycopg2.extras
from fastapi import APIRouter, Path, Query, HTTPException
from core.db import get_connection, release_connection

router = APIRouter()

@router.get("/{entity}/full_data")
def get_entity_full_data(
  entity: str = Path(..., description="Entity type: season, region, grower, organizer"),
  category_id: int = Query(...)
):

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Validate entity type
        entity = entity.lower()
        # Get category details
        cur.execute("SELECT category_name FROM operations.categories WHERE category_id = %s", (category_id,))
        cat_row = cur.fetchone()
        if not cat_row:
            raise HTTPException(status_code=404, detail="Invalid category_id")

        table_name = cat_row['category_name'].lower()

        # 2. Check if table exists (optional, but user-friendly)
        cur.execute("""
            SELECT to_regclass(%s)
        """, (f"operations.{table_name}",))
        if not cur.fetchone()[0]:
            raise HTTPException(status_code=404, detail=f"Table for entity '{entity}' does not exist")

        # 3. Get column metadata
        cur.execute("""
            SELECT db_column_name, display_name, type, group_name, is_visible
            FROM operations.column_metadata
            WHERE category_id = %s
        """, (category_id,))
        metadata = cur.fetchall()
        columns = [dict(col) for col in metadata]
        ordered_cols = [col["db_column_name"] for col in metadata]

        # 4. Fetch data (no extra "_records" here)
        cur.execute(
            f"""SELECT {', '.join(ordered_cols)} FROM operations.{table_name} WHERE category_id = %s ORDER BY {entity}_id""",
            (category_id,)
        )
        rows = [dict(row) for row in cur.fetchall()]

        return {
            "columns": columns,
            "data": rows
        }
    finally:
        release_connection(conn)

