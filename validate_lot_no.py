"""
Quick validation script to check lot_no population in inspection_level tables
"""
from core.db import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    print("=" * 80)
    print("Validating lot_no Population in Inspection Tables")
    print("=" * 80)
    
    for level in range(1, 7):
        table_name = f"inspection_level_{level}"
        
        # Check total rows
        total_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name}"))
        total_rows = total_result.scalar()
        
        # Check NULL/empty lot_no
        null_result = db.execute(text(f"""
            SELECT COUNT(*) 
            FROM operations.{table_name} 
            WHERE lot_no IS NULL OR lot_no = '' OR TRIM(lot_no) = ''
        """))
        null_count = null_result.scalar()
        
        # Check non-null lot_no
        non_null_result = db.execute(text(f"""
            SELECT COUNT(DISTINCT lot_no) 
            FROM operations.{table_name} 
            WHERE lot_no IS NOT NULL AND lot_no != '' AND TRIM(lot_no) != ''
        """))
        distinct_lot_no = non_null_result.scalar()
        
        print(f"\n{table_name}:")
        print(f"  Total rows: {total_rows:,}")
        print(f"  NULL/empty lot_no: {null_count:,}")
        print(f"  Non-null lot_no: {total_rows - null_count:,}")
        print(f"  Distinct lot_no values: {distinct_lot_no:,}")
        
        if null_count > 0:
            print(f"  ⚠ WARNING: {null_count} rows have NULL/empty lot_no!")
        else:
            print(f"  ✓ All lot_no values are populated")
    
    # Check inspection_sheet tables
    print("\n" + "=" * 80)
    print("Validating inspection_sheet Tables")
    print("=" * 80)
    
    for level in range(1, 7):
        table_name = f"inspection_sheet_{level}"
        
        # Check total rows
        total_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name}"))
        total_rows = total_result.scalar()
        
        # Check NULL/empty lot_no
        null_result = db.execute(text(f"""
            SELECT COUNT(*) 
            FROM operations.{table_name} 
            WHERE lot_no IS NULL OR lot_no = '' OR TRIM(lot_no) = ''
        """))
        null_count = null_result.scalar()
        
        # Check NULL/empty s1_lot_no
        s1_null_result = db.execute(text(f"""
            SELECT COUNT(*) 
            FROM operations.{table_name} 
            WHERE s1_lot_no IS NULL OR s1_lot_no = '' OR TRIM(s1_lot_no) = ''
        """))
        s1_null_count = s1_null_result.scalar()
        
        print(f"\n{table_name}:")
        print(f"  Total rows: {total_rows:,}")
        print(f"  NULL/empty lot_no: {null_count:,}")
        print(f"  NULL/empty s1_lot_no: {s1_null_count:,}")
        
        if null_count > 0 or s1_null_count > 0:
            print(f"  ⚠ WARNING: Found NULL/empty lot numbers!")
        else:
            print(f"  ✓ All lot_no and s1_lot_no values are populated")
    
    print("\n" + "=" * 80)
    print("Validation Complete")
    print("=" * 80)
    
finally:
    db.close()

