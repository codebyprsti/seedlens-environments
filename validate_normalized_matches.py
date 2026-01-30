"""
Validation script to check normalized lot_no matching
"""
from core.db import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    print("=" * 80)
    print("Validating Normalized lot_no Matching")
    print("=" * 80)
    
    # Check inspection_level_1 row count
    result1 = db.execute(text("SELECT COUNT(*) FROM operations.inspection_level_1"))
    level1_count = result1.scalar()
    print(f"\ninspection_level_1: {level1_count:,} rows")
    
    if level1_count == 0:
        print("ERROR: inspection_level_1 has 0 rows! It needs to be reloaded.")
        db.close()
        exit(1)
    
    # Check for NULL lot_no in level 1
    null_result = db.execute(text("SELECT COUNT(*) FROM operations.inspection_level_1 WHERE lot_no IS NULL OR lot_no = ''"))
    null_count = null_result.scalar()
    print(f"  NULL/empty lot_no: {null_count:,}")
    
    # Check sample lot_no values to verify normalization
    sample_result = db.execute(text("SELECT DISTINCT lot_no FROM operations.inspection_level_1 LIMIT 5"))
    samples = [row[0] for row in sample_result.fetchall()]
    print(f"  Sample lot_no values: {samples}")
    
    # Check if any lot_no values have uppercase or leading/trailing spaces (should be none)
    uppercase_result = db.execute(text("""
        SELECT COUNT(*) 
        FROM operations.inspection_level_1 
        WHERE lot_no != LOWER(TRIM(lot_no))
    """))
    uppercase_count = uppercase_result.scalar()
    print(f"  Non-normalized lot_no values: {uppercase_count:,}")
    
    if uppercase_count > 0:
        print("  WARNING: Found non-normalized lot_no values!")
    else:
        print("  ✓ All lot_no values are normalized (lowercase, trimmed)")
    
    # Check match counts for each level
    print("\n" + "=" * 80)
    print("Match Counts (using direct lot_no comparison)")
    print("=" * 80)
    
    expected_counts = {
        2: 25818,
        3: 25143,
        4: 6776,
        5: 25034,
        6: 24586
    }
    
    for level in range(2, 7):
        match_query = text(f"""
            SELECT COUNT(*)
            FROM operations.inspection_level_1 il1
            JOIN operations.inspection_level_{level} il{level}
              ON il1.lot_no = il{level}.lot_no
        """)
        result = db.execute(match_query)
        match_count = result.scalar()
        expected = expected_counts.get(level, 0)
        status = "✓" if match_count == expected else "✗"
        diff = match_count - expected
        print(f"\nLevel {level}:")
        print(f"  Matches: {match_count:,}")
        print(f"  Expected: {expected:,}")
        print(f"  Difference: {diff:+,}")
        print(f"  Status: {status}")
        
        if match_count != expected:
            print(f"  ⚠ MISMATCH: Expected {expected:,}, got {match_count:,}")
    
    print("\n" + "=" * 80)
    print("Validation Complete")
    print("=" * 80)
    
finally:
    db.close()

