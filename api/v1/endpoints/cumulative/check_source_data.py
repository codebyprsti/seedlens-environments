"""Check source table data"""
import sys
import os
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.db import get_connection, release_connection
import psycopg2

conn = get_connection()
try:
    cursor = conn.cursor()
    
    print("=" * 60)
    print("Source Table Data Check")
    print("=" * 60)
    
    # Check source tables
    for level in range(1, 7):
        table_name = f"inspection_level_{level}"
        cursor.execute(f"SELECT COUNT(*) FROM operations.{table_name}")
        count = cursor.fetchone()[0]
        
        # Check lot_id column
        cursor.execute(f"""
            SELECT COUNT(*) 
            FROM operations.{table_name} 
            WHERE lot_id IS NOT NULL
        """)
        lot_count = cursor.fetchone()[0]
        
        print(f"\n{table_name}:")
        print(f"  Total rows: {count:,}")
        print(f"  Rows with lot_id: {lot_count:,}")
        
        # Sample lot_ids
        if lot_count > 0:
            cursor.execute(f"""
                SELECT DISTINCT lot_id 
                FROM operations.{table_name} 
                WHERE lot_id IS NOT NULL 
                LIMIT 5
            """)
            samples = cursor.fetchall()
            print(f"  Sample lot_ids: {[s[0] for s in samples]}")
    
    cursor.close()
finally:
    release_connection(conn)

