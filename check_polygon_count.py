"""Quick check: which DB the app uses and row count in operations.location_polygons."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from core.db import get_connection, release_connection

def main():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT current_database(), inet_server_addr()::text")
        row = cur.fetchone()
        db_name, host = row[0], row[1]
        cur.execute("SELECT COUNT(*) FROM operations.location_polygons")
        count = cur.fetchone()[0]
        cur.execute("SELECT location_id, polygon_index, village, district FROM operations.location_polygons ORDER BY location_id, polygon_index LIMIT 10")
        rows = cur.fetchall()
        print("Database:", db_name)
        print("Host:", host)
        print("operations.location_polygons COUNT:", count)
        print("First rows (up to 10):")
        for r in rows:
            print(" ", r)
    finally:
        cur.close()
        release_connection(conn)

if __name__ == "__main__":
    main()
