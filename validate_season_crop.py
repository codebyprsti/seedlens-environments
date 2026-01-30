"""
Validation script to check season and crop population in inspection_level tables
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.absolute()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.db import SessionLocal
from sqlalchemy import text
import pandas as pd

# Configure UTF-8 output
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db = SessionLocal()
try:
    print("=" * 80)
    print("VALIDATION: Season and Crop Population")
    print("=" * 80)
    
    # 1. Count NULL season/crop per inspection_level table
    print("\n1. NULL COUNT CHECK")
    print("-" * 80)
    for level in range(1, 7):
        table_name = f"inspection_level_{level}"
        
        try:
            # Check total rows
            total_result = db.execute(text(f"SELECT COUNT(*) FROM operations.{table_name}"))
            total_rows = total_result.scalar()
            
            # Check season NULL count
            season_null_result = db.execute(text(f"""
                SELECT COUNT(*) FROM operations.{table_name} 
                WHERE season IS NULL OR season = ''
            """))
            season_null = season_null_result.scalar()
            
            # Check crop NULL count
            crop_null_result = db.execute(text(f"""
                SELECT COUNT(*) FROM operations.{table_name} 
                WHERE crop IS NULL OR crop = ''
            """))
            crop_null = crop_null_result.scalar()
            
            # Check non-null counts
            season_non_null = total_rows - season_null
            crop_non_null = total_rows - crop_null
            
            print(f"\n{table_name}:")
            print(f"  Total rows: {total_rows:,}")
            print(f"  Season: {season_non_null:,} non-null, {season_null:,} NULL ({season_null/total_rows*100:.1f}% NULL)" if total_rows > 0 else "  Season: N/A")
            print(f"  Crop: {crop_non_null:,} non-null, {crop_null:,} NULL ({crop_null/total_rows*100:.1f}% NULL)" if total_rows > 0 else "  Crop: N/A")
            
            if level == 1:
                if season_null == total_rows:
                    print(f"  ERROR: Level 1 has ALL NULL season values!")
                if crop_null == total_rows:
                    print(f"  ERROR: Level 1 has ALL NULL crop values!")
        except Exception as e:
            print(f"\n{table_name}: Error - {e}")
    
    # 2. Print distinct season/crop values
    print("\n\n2. DISTINCT VALUES CHECK")
    print("-" * 80)
    for level in range(1, 7):
        table_name = f"inspection_level_{level}"
        
        try:
            # Get distinct season values
            season_result = db.execute(text(f"""
                SELECT DISTINCT season, COUNT(*) as cnt
                FROM operations.{table_name}
                WHERE season IS NOT NULL AND season != ''
                GROUP BY season
                ORDER BY cnt DESC
                LIMIT 10
            """))
            seasons = season_result.fetchall()
            
            # Get distinct crop values
            crop_result = db.execute(text(f"""
                SELECT DISTINCT crop, COUNT(*) as cnt
                FROM operations.{table_name}
                WHERE crop IS NOT NULL AND crop != ''
                GROUP BY crop
                ORDER BY cnt DESC
                LIMIT 10
            """))
            crops = crop_result.fetchall()
            
            print(f"\n{table_name}:")
            if seasons:
                print(f"  Top Season values:")
                for season, cnt in seasons:
                    print(f"    '{season}': {cnt:,} rows")
            else:
                print(f"  No season values found")
            
            if crops:
                print(f"  Top Crop values:")
                for crop, cnt in crops:
                    print(f"    '{crop}': {cnt:,} rows")
            else:
                print(f"  No crop values found")
        except Exception as e:
            print(f"\n{table_name}: Error - {e}")
    
    # 3. Compare counts vs Excel file (if available)
    print("\n\n3. EXCEL FILE COMPARISON")
    print("-" * 80)
    excel_file = r"C:\Users\madan\OneDrive\Documents\Inspection Reports Production RABI 21-25 decoded (1).xlsx"
    
    try:
        if not Path(excel_file).exists():
            print(f"Excel file not found: {excel_file}")
            print("Skipping Excel comparison")
        else:
            print(f"Reading Excel file: {excel_file}")
            excel_data = pd.ExcelFile(excel_file, engine='openpyxl')
            sheet_names = excel_data.sheet_names
            
            print(f"Found {len(sheet_names)} sheets: {sheet_names}")
            
            for level in range(1, min(7, len(sheet_names) + 1)):
                sheet_name = sheet_names[level - 1] if level <= len(sheet_names) else None
                if sheet_name:
                    try:
                        df = pd.read_excel(excel_file, sheet_name=sheet_name, nrows=1000)  # Sample first 1000 rows
                        
                        # Check for season columns
                        season_cols = [col for col in df.columns if 'season' in str(col).lower()]
                        crop_cols = [col for col in df.columns if 'crop' in str(col).lower() and 'condition' not in str(col).lower() and 'previous' not in str(col).lower()]
                        
                        print(f"\nSheet {level} ('{sheet_name}'):")
                        print(f"  Total columns: {len(df.columns)}")
                        print(f"  Season-like columns: {season_cols}")
                        print(f"  Crop-like columns: {crop_cols}")
                        
                        if season_cols:
                            sample_season = df[season_cols[0]].dropna().head(5).tolist()
                            print(f"  Sample season values: {sample_season}")
                        if crop_cols:
                            sample_crop = df[crop_cols[0]].dropna().head(5).tolist()
                            print(f"  Sample crop values: {sample_crop}")
                    except Exception as e:
                        print(f"  Error reading sheet {level}: {e}")
    except Exception as e:
        print(f"Error reading Excel file: {e}")
    
    print("\n" + "=" * 80)
    print("VALIDATION COMPLETE")
    print("=" * 80)
    
finally:
    db.close()
