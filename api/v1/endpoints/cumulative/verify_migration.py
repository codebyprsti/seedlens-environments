"""Quick verification script to check if migration tables exist"""
import sys
import os
# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.db import get_connection, release_connection
import psycopg2

conn = get_connection()
try:
    cursor = conn.cursor()
    
    # Check tables
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'operations' 
        AND table_name LIKE 'inspection_sheet_%' 
        ORDER BY table_name
    """)
    tables = cursor.fetchall()
    
    print("=" * 60)
    print("Migration Verification")
    print("=" * 60)
    print(f"\nCreated tables ({len(tables)}):")
    for table in tables:
        print(f"  - {table[0]}")
    
    # Check row counts
    print("\nRow counts:")
    for table in tables:
        table_name = table[0]
        cursor.execute(f"SELECT COUNT(*) FROM operations.{table_name}")
        count = cursor.fetchone()[0]
        print(f"  {table_name}: {count:,} rows")
    
    # Check columns in first table
    print("\nSample columns from inspection_sheet_1:")
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema = 'operations' 
        AND table_name = 'inspection_sheet_1'
        ORDER BY ordinal_position
        LIMIT 10
    """)
    columns = cursor.fetchall()
    for col in columns:
        print(f"  - {col[0]}")
    
    print("\n" + "=" * 60)
    print("Migration verification complete!")
    print("=" * 60)
    
    cursor.close()
finally:
    release_connection(conn)

