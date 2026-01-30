"""
Validation script to verify fixes for season/crop population and lot_no normalization
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.absolute()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.db import SessionLocal
from sqlalchemy import text

# Configure UTF-8 output
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db = SessionLocal()
try:
    print("=" * 80)
    print("VALIDATION: Season/Crop Population and Lot Number Normalization")
    print("=" * 80)
    
    # 1. Check season and crop population in inspection_level tables
    print("\n1. SEASON AND CROP POPULATION CHECK")
    print("-" * 80)
    for level in range(1, 7):
        table_name = f"inspection_level_{level}"
        
        try:
            # Check total rows
            total_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name}"))
            total_rows = total_result.scalar()
            
            # Check season NULL count
            season_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name} WHERE season IS NULL"))
            season_null = season_result.scalar()
            
            # Check crop NULL count
            crop_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name} WHERE crop IS NULL"))
            crop_null = crop_result.scalar()
            
            print(f"\n{table_name}:")
            print(f"  Total rows: {total_rows:,}")
            print(f"  Season NULL: {season_null:,} ({season_null/total_rows*100:.2f}%)" if total_rows > 0 else "  Season NULL: N/A")
            print(f"  Crop NULL: {crop_null:,} ({crop_null/total_rows*100:.2f}%)" if total_rows > 0 else "  Crop NULL: N/A")
            
            if level == 1:
                if season_null > 0:
                    print(f"  WARNING: Level 1 has {season_null} NULL season values - this will break cumulative matching")
                if crop_null > 0:
                    print(f"  WARNING: Level 1 has {crop_null} NULL crop values - this will break cumulative matching")
            else:
                if season_null == total_rows:
                    print(f"  INFO: All season values are NULL (will be populated from level 1 during migration)")
                if crop_null == total_rows:
                    print(f"  INFO: All crop values are NULL (will be populated from level 1 during migration)")
        except Exception as e:
            print(f"\n{table_name}: Error - {e}")
    
    # 2. Check lot_no normalization and NULL values
    print("\n\n2. LOT_NUMBER NORMALIZATION CHECK")
    print("-" * 80)
    for level in range(1, 7):
        table_name = f"inspection_level_{level}"
        
        # Check total rows
        total_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name}"))
        total_rows = total_result.scalar()
        
        # Check lot_no NULL count
        lot_null_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name} WHERE lot_no IS NULL"))
        lot_null = lot_null_result.scalar()
        
        # Check if lot_no values are normalized (lowercase, trimmed)
        # Sample some lot_no values to check normalization
        sample_result = db.execute(text(f"""
            SELECT DISTINCT lot_no 
            FROM operations.{table_name} 
            WHERE lot_no IS NOT NULL 
            LIMIT 10
        """))
        sample_lots = [row[0] for row in sample_result]
        
        # Check if any samples have uppercase or leading/trailing spaces
        non_normalized = []
        for lot in sample_lots:
            if lot and (lot != lot.lower() or lot != lot.strip()):
                non_normalized.append(lot)
        
        print(f"\n{table_name}:")
        print(f"  Total rows: {total_rows:,}")
        print(f"  lot_no NULL: {lot_null:,} ({lot_null/total_rows*100:.2f}%)" if total_rows > 0 else "  lot_no NULL: N/A")
        
        if level == 1:
            if lot_null > 0:
                print(f"  ERROR: Level 1 has {lot_null} NULL lot_no values - REJECTED during load")
            else:
                print(f"  OK: Level 1: All lot_no values are non-NULL")
        
        if sample_lots:
            print(f"  Sample lot_no values: {sample_lots[:5]}")
            if non_normalized:
                print(f"  WARNING: Found non-normalized lot_no values: {non_normalized}")
            else:
                print(f"  OK: All sampled lot_no values are normalized (lowercase, trimmed)")
    
    # 3. Check match counts using normalized lot_no
    print("\n\n3. MATCH COUNTS (Using Normalized lot_no)")
    print("-" * 80)
    expected_counts = {
        2: 25818,
        3: 25143,
        4: 6776,
        5: 25034,
        6: 24586
    }
    
    for level in range(2, 7):
        table_name = f"inspection_level_{level}"
        
        # Get lot column name for level 1 and current level
        # Try to detect lot column names
        lot_col_1 = "lot_no"  # Default
        lot_col_level = "lot_no"  # Default
        
        # Check if mrno_lot_no exists
        try:
            check_result = db.execute(text(f"""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_schema = 'operations' 
                AND table_name = 'inspection_level_{level}' 
                AND column_name IN ('lot_no', 'mrno_lot_no', 'lot_id', 'lot_number')
                LIMIT 1
            """))
            col_row = check_result.fetchone()
            if col_row:
                lot_col_level = col_row[0]
        except:
            pass
        
        # Count matches using normalized lot_no (LOWER(TRIM()))
        match_result = db.execute(text(f"""
            SELECT COUNT(*) 
            FROM operations.inspection_level_1 il1
            INNER JOIN operations.{table_name} il{level}
            ON LOWER(TRIM(CAST(il1.lot_no AS TEXT))) = LOWER(TRIM(CAST(il{level}.{lot_col_level} AS TEXT)))
            WHERE il1.lot_no IS NOT NULL 
            AND il{level}.{lot_col_level} IS NOT NULL
        """))
        match_count = match_result.scalar()
        
        expected = expected_counts.get(level, 0)
        difference = match_count - expected
        
        print(f"\nLevel {level} matches:")
        print(f"  Expected: {expected:,}")
        print(f"  Actual: {match_count:,}")
        print(f"  Difference: {difference:+,}")
        if abs(difference) <= 5:
            print(f"  OK: Match count is correct (within 5 rows)")
        else:
            print(f"  WARNING: Match count differs by {abs(difference)} rows")
    
    # 4. Check row counts consistency
    print("\n\n4. ROW COUNT CONSISTENCY CHECK")
    print("-" * 80)
    
    # Get level 1 row count
    level1_result = db.execute(text("SELECT COUNT(*) FROM operations.inspection_level_1"))
    level1_count = level1_result.scalar()
    
    print(f"\ninspection_level_1: {level1_count:,} rows (master table)")
    
    for level in range(2, 7):
        table_name = f"inspection_level_{level}"
        result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name}"))
        count = result.scalar()
        difference = count - level1_count
        
        print(f"inspection_level_{level}: {count:,} rows (difference: {difference:+,})")
        
        if abs(difference) > 0:
            print(f"  ⚠️  WARNING: Row count differs from level 1 by {abs(difference)} rows")
            print(f"  ℹ️  INFO: This is expected - levels 2-6 may have different counts")
            print(f"  ℹ️  INFO: During migration, all inspection_sheet tables will have {level1_count} rows")
    
    # 5. Check inspection_sheet tables for season/crop
    print("\n\n5. INSPECTION_SHEET TABLES - SEASON/CROP CHECK")
    print("-" * 80)
    
    for level in range(1, 7):
        table_name = f"inspection_sheet_{level}"
        
        try:
            # Check if table exists
            exists_result = db.execute(text(f"""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = 'operations' 
                AND table_name = '{table_name}'
            """))
            if exists_result.scalar() == 0:
                print(f"\n{table_name}: Table does not exist yet")
                continue
            
            # Check total rows
            total_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name}"))
            total_rows = total_result.scalar()
            
            # Check s1_season and s1_crop (should be populated from inspection_level_1)
            season_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name} WHERE s1_season IS NULL"))
            season_null = season_result.scalar()
            
            crop_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name} WHERE s1_crop IS NULL"))
            crop_null = crop_result.scalar()
            
            print(f"\n{table_name}:")
            print(f"  Total rows: {total_rows:,}")
            print(f"  s1_season NULL: {season_null:,} ({season_null/total_rows*100:.2f}%)" if total_rows > 0 else "  s1_season NULL: N/A")
            print(f"  s1_crop NULL: {crop_null:,} ({crop_null/total_rows*100:.2f}%)" if total_rows > 0 else "  s1_crop NULL: N/A")
            
            if season_null == 0 and crop_null == 0:
                print(f"  OK: All season and crop values are populated")
            elif season_null > 0 or crop_null > 0:
                print(f"  WARNING: Some season/crop values are NULL")
        except Exception as e:
            print(f"\n{table_name}: Error checking - {e}")
    
    print("\n" + "=" * 80)
    print("VALIDATION COMPLETE")
    print("=" * 80)
    
finally:
    db.close()
