"""Check location_id pattern"""
from core.db import get_connection, release_connection
import psycopg2.extras

conn = get_connection()
cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

try:
    cur.execute("""
        SELECT location_id 
        FROM operations.locations 
        ORDER BY location_id DESC 
        LIMIT 20
    """)
    ids = cur.fetchall()
    print('Sample location_ids:')
    for i in ids:
        print(f"  {i['location_id']}")
finally:
    cur.close()
    release_connection(conn)

