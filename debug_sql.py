"""Debug script to check the actual SQL being generated"""
from core.db import get_connection, release_connection
from api.v1.endpoints.cumulative.migration import get_all_columns_from_table, populate_optimized_table, detect_lot_column

conn = get_connection()
try:
    # Get column info
    all_columns = {}
    lot_columns = {}
    for level in range(1, 7):
        table_name = f"inspection_level_{level}"
        columns = get_all_columns_from_table(conn, table_name)
        all_columns[level] = columns
        lot_col = detect_lot_column(conn, table_name)
        lot_columns[level] = lot_col
    
    # Generate SQL for level 1
    level = 1
    base_lot_col = lot_columns[1]
    
    # Check inspection_level column info
    il_col = [c for c in all_columns[1] if c['column_name'] == 'inspection_level'][0]
    print(f"inspection_level column info:")
    print(f"  udt_name: {il_col.get('udt_name')}")
    print(f"  data_type: {il_col.get('data_type')}")
    
    # Check what NULL expression would be generated
    udt_name = il_col.get('udt_name')
    if udt_name and udt_name.lower() == 'int4':
        null_expr = "NULL::integer"
        print(f"  Generated NULL expression: {null_expr}")
    else:
        print(f"  WARNING: udt_name is {udt_name}, not int4!")
    
    # Try to generate the actual SQL
    print("\nGenerating SQL for inspection_sheet_1...")
    # This will fail, but we can see the SQL
    try:
        populate_optimized_table(conn, 1, all_columns, lot_columns)
    except Exception as e:
        print(f"Error (expected): {e}")
finally:
    release_connection(conn)

