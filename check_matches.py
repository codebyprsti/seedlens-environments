"""
Check match counts after normalization
"""
from core.db import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    print("=" * 80)
    print("Match Counts After Normalization")
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
        status = "OK" if match_count == expected else "MISMATCH"
        diff = match_count - expected
        print(f"\nLevel {level}:")
        print(f"  Matches: {match_count:,}")
        print(f"  Expected: {expected:,}")
        print(f"  Difference: {diff:+,}")
        print(f"  Status: {status}")
        
        if match_count != expected:
            print(f"  WARNING: Expected {expected:,}, got {match_count:,}")
    
    print("\n" + "=" * 80)
    
finally:
    db.close()

