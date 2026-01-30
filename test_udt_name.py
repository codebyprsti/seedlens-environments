"""Test script to verify udt_name retrieval"""
from core.db import get_connection, release_connection
from api.v1.endpoints.cumulative.migration import get_all_columns_from_table

conn = get_connection()
try:
    cols = get_all_columns_from_table(conn, 'inspection_level_1')
    il_col = [c for c in cols if c['column_name'] == 'inspection_level'][0]
    print(f"inspection_level column info:")
    print(f"  column_name: {il_col.get('column_name')}")
    print(f"  data_type: {il_col.get('data_type')}")
    print(f"  udt_name: {il_col.get('udt_name')}")
    print(f"  max_length: {il_col.get('max_length')}")
    
    # Check a few more columns
    print("\nSample columns:")
    for col in cols[:5]:
        print(f"  {col['column_name']}: data_type={col.get('data_type')}, udt_name={col.get('udt_name')}")
finally:
    release_connection(conn)

