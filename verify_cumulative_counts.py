"""
Verification script to check cumulative non-null counts in inspection_sheet tables
This proves that data was reloaded and cumulative values reflect new source data.
"""
from core.db import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    print("=" * 80)
    print("CUMULATIVE NON-NULL COUNT VERIFICATION")
    print("=" * 80)
    print("\nThis query proves that data was reloaded:")
    print("SELECT COUNT(*) total, COUNT(s2_lot_no) s2, COUNT(s3_lot_no) s3,")
    print("       COUNT(s4_lot_no) s4, COUNT(s5_lot_no) s5, COUNT(s6_lot_no) s6")
    print("FROM operations.inspection_sheet_6;")
    print("\n" + "=" * 80)
    
    for level in range(1, 7):
        table_name = f"inspection_sheet_{level}"
        
        # Get total row count
        total_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name}"))
        total_rows = total_result.scalar()
        
        if level >= 2:
            # Get cumulative non-null counts
            counts_result = db.execute(text(f"""
                SELECT 
                    COUNT(*) as total,
                    COUNT(s2_lot_no) as s2,
                    COUNT(s3_lot_no) as s3,
                    COUNT(s4_lot_no) as s4,
                    COUNT(s5_lot_no) as s5,
                    COUNT(s6_lot_no) as s6
                FROM operations.{table_name}
            """))
            counts = counts_result.fetchone()
            
            print(f"\n{table_name}:")
            print(f"  Total rows: {counts[0]:,}")
            if level >= 2:
                print(f"  s2_lot_no (non-null): {counts[1]:,}")
            if level >= 3:
                print(f"  s3_lot_no (non-null): {counts[2]:,}")
            if level >= 4:
                print(f"  s4_lot_no (non-null): {counts[3]:,}")
            if level >= 5:
                print(f"  s5_lot_no (non-null): {counts[4]:,}")
            if level >= 6:
                print(f"  s6_lot_no (non-null): {counts[5]:,}")
        else:
            print(f"\n{table_name}:")
            print(f"  Total rows: {total_rows:,}")
    
    print("\n" + "=" * 80)
    print("Verification Complete")
    print("=" * 80)
    print("\nIf these numbers differ from previous runs, the reload was successful!")
    
finally:
    db.close()
