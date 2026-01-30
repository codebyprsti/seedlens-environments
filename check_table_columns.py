"""Check columns in season_crop_inspection_final table"""
from core.db import get_connection, release_connection
import psycopg2.extras

conn = get_connection()
cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

try:
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema = 'operations' 
        AND table_name = 'season_crop_inspection_final' 
        ORDER BY ordinal_position
    """)
    cols = cur.fetchall()
    print('Columns in season_crop_inspection_final:')
    for c in cols:
        print(f"  {c['column_name']}")
finally:
    cur.close()
    release_connection(conn)

