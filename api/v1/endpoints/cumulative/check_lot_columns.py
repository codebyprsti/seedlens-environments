"""Check which lot columns have data"""
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
    print("Lot Column Data Check")
    print("=" * 60)
    
    lot_columns = ["lot_id", "lot_no", "lot_number", "mrno_lot_no"]
    
    for level in range(1, 7):
        table_name = f"inspection_level_{level}"
        print(f"\n{table_name}:")
        
        # Check which lot columns exist and have data
        cursor.execute(f"""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_schema = 'operations' 
            AND table_name = '{table_name}'
            AND column_name IN ('lot_id', 'lot_no', 'lot_number', 'mrno_lot_no')
        """)
        existing_cols = [row[0] for row in cursor.fetchall()]
        
        for col in existing_cols:
            cursor.execute(f"""
                SELECT COUNT(*) 
                FROM operations.{table_name} 
                WHERE {col} IS NOT NULL 
                AND {col} != ''
            """)
            count = cursor.fetchone()[0]
            print(f"  {col}: {count:,} non-null values")
            
            if count > 0:
                cursor.execute(f"""
                    SELECT DISTINCT {col} 
                    FROM operations.{table_name} 
                    WHERE {col} IS NOT NULL 
                    AND {col} != ''
                    LIMIT 3
                """)
                samples = cursor.fetchall()
                print(f"    Samples: {[str(s[0])[:20] for s in samples]}")
    
    cursor.close()
finally:
    release_connection(conn)

