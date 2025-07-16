import csv
import io
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import UploadFile, HTTPException
from core.db import get_connection, release_connection
import psycopg2.extras


class CategoryService:
    @staticmethod
    def create_category_and_table(
        conn,
        category_name: str,
        category_desc: str,
        header_clean: List[str],
        col_type_list: List[str]
    ) -> int:
        """Create a new category and associated table in the database."""
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO seedworks.category(category_name, description)
                VALUES(%s, %s) RETURNING category_id""",
                (category_name, category_desc)
            )
            category_id = cur.fetchone()[0]

            for i, col in enumerate(header_clean):
                cur.execute(
                    """INSERT INTO seedworks.column_metadata(
                        category_id, db_column, display_name, type,
                        group_name, is_visible, create_date
                    ) VALUES(%s, %s, %s, %s, %s, %s, %s)""",
                    (category_id, col, col, col_type_list[i].strip(), None, True, datetime.utcnow())
                )

            table_name = f"{category_name.lower().replace(' ', '_')}_records"
            col_defs = ", ".join(
                f"{col} {typ.strip()}" for col, typ in zip(header_clean, col_type_list)
            )
            cur.execute(
                f"""CREATE TABLE IF NOT EXISTS seedworks.{table_name} (
                    season_id SERIAL PRIMARY KEY,
                    category_id INT REFERENCES seedworks.category(category_id) ON DELETE CASCADE,
                    {col_defs},
                    create_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            return category_id, table_name

    @staticmethod
    def insert_csv_data(conn, table_name: str, category_id: int, header_clean: List[str], reader):
        """Insert data from CSV into the newly created table."""
        with conn.cursor() as cur:
            insert_sql = (
                f"INSERT INTO seedworks.{table_name} "
                f"(category_id, {', '.join(header_clean)}) "
                f"VALUES (%s, {', '.join(['%s'] * len(header_clean))})"
            )
            for row in reader:
                cur.execute(insert_sql, (category_id, *row))


class RecordService:
    def __init__(self):
        pass

    def get_category_info(self, conn, category_id: int) -> str:
        """Get table name associated with a category."""
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT category_name FROM seedworks.category WHERE category_id = %s",
                (category_id,)
            )
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Invalid category_id")
            return f"{result['category_name'].lower().replace(' ', '_').rstrip('s')}_records"

    def add_record(self, conn, category_id: int, data: Dict) -> Dict:
        """Add a new record to the specified category."""
        if not data:
            raise HTTPException(status_code=400, detail="No data to insert")

        table_name = self.get_category_info(conn, category_id)
        columns = list(data.keys())
        values = list(data.values())
        columns.append("category_id")
        values.append(category_id)

        col_names = ", ".join(f'"{col}"' for col in columns)
        placeholders = ", ".join(["%s"] * len(values))
        sql = (
            f'INSERT INTO seedworks.{table_name} ({col_names}) '
            f'VALUES ({placeholders}) RETURNING *'
        )

        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(sql, values)
            inserted_row = cur.fetchone()
            conn.commit()
            return dict(inserted_row)


def process_csv_upload(
    category_name: str,
    category_desc: str,
    column_types: str,
    file: UploadFile
) -> Dict:
    """Process CSV upload and create new category with data."""
    contents = file.file.read()
    reader = csv.reader(io.StringIO(contents.decode("utf-8")))
    header = next(reader)
    header_clean = [col.strip().replace(' ', '_') for col in header]
    col_type_list = column_types.split(',')

    if len(header_clean) != len(col_type_list):
        raise HTTPException(
            status_code=400,
            detail="Number of columns in CSV doesn't match provided column types"
        )

    conn = get_connection()
    try:
        category_id, table_name = CategoryService.create_category_and_table(
            conn, category_name, category_desc, header_clean, col_type_list
        )
        CategoryService.insert_csv_data(conn, table_name, category_id, header_clean, reader)
        conn.commit()
        return {"status": "Uploaded and processed", "table": table_name}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        release_connection(conn)


def list_all_categories() -> List[Dict]:
    """List all available categories."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT category_id, category_name FROM seedworks.category")
            return [{"id": r["category_id"], "name": r["category_name"]} for r in cur.fetchall()]
    finally:
        release_connection(conn)


def get_records_for_category(category_id: int, page: int, page_size: int) -> Dict:
    """Get paginated records for a specific category."""
    offset = (page - 1) * page_size
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            # Get category info
            cur.execute(
                "SELECT category_name FROM seedworks.category WHERE category_id = %s",
                (category_id,)
            )
            category = cur.fetchone()
            if not category:
                raise HTTPException(status_code=404, detail="Invalid category_id")

            # Get column metadata
            table_name = f"{category['category_name'].lower().replace(' ', '_')}_records"
            cur.execute(
                "SELECT * FROM seedworks.column_metadata WHERE category_id = %s",
                (category_id,)
            )
            meta_rows = cur.fetchall()

            # Organize columns by visibility
            visible = [dict(r) for r in meta_rows if r['is_visible']]
            invisible = [dict(r) for r in meta_rows if not r['is_visible']]
            ordered_cols = visible + invisible
            ordered_col_names = [r['db_column'] for r in ordered_cols]

            # Fetch paginated data
            query = f"""
                SELECT {', '.join(ordered_col_names)}
                FROM seedworks.{table_name}
                WHERE category_id = %s
                ORDER BY season_id LIMIT %s OFFSET %s
            """
            cur.execute(query, (category_id, page_size, offset))
            rows = cur.fetchall()

            return {
                "columns": ordered_cols,
                "rows": [dict(r) for r in rows],
                "pagination": {
                    "page": page,
                    "page_size": page_size
                }
            }
    finally:
        release_connection(conn)
