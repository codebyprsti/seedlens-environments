"""
Validation script to check column mapping during ingestion
"""
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.absolute()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pandas as pd
from services.reload_inspection_tables import read_excel_sheet, prepare_dataframe_for_inspection_level

# Configure UTF-8 output
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

excel_file = r"C:\Users\madan\OneDrive\Documents\Inspection Reports Production RABI 21-25 decoded (1).xlsx"

try:
    print("=" * 80)
    print("VALIDATION: Ingestion Column Mapping")
    print("=" * 80)
    
    if not Path(excel_file).exists():
        print(f"Excel file not found: {excel_file}")
        sys.exit(1)
    
    # Read Excel file
    excel_data = pd.ExcelFile(excel_file, engine='openpyxl')
    sheet_names = excel_data.sheet_names
    
    print(f"Found {len(sheet_names)} sheets: {sheet_names}\n")
    
    for level in range(1, min(7, len(sheet_names) + 1)):
        sheet_name = sheet_names[level - 1] if level <= len(sheet_names) else None
        if not sheet_name:
            continue
        
        print("=" * 80)
        print(f"LEVEL {level} - Sheet: '{sheet_name}'")
        print("=" * 80)
        
        try:
            # Read raw Excel sheet
            df_raw = read_excel_sheet(excel_file, sheet_name)
            
            print(f"\n1. RAW EXCEL COLUMNS ({len(df_raw.columns)} total):")
            print("-" * 80)
            for i, col in enumerate(df_raw.columns[:30], 1):  # Show first 30
                print(f"  {i}. '{col}'")
            if len(df_raw.columns) > 30:
                print(f"  ... and {len(df_raw.columns) - 30} more columns")
            
            # Check for season/crop columns in raw data
            season_cols_raw = [col for col in df_raw.columns if 'season' in str(col).lower()]
            crop_cols_raw = [col for col in df_raw.columns if 'crop' in str(col).lower() and 'condition' not in str(col).lower() and 'previous' not in str(col).lower()]
            
            print(f"\n  Season-like columns found: {season_cols_raw}")
            print(f"  Crop-like columns found: {crop_cols_raw}")
            
            # Process through prepare_dataframe_for_inspection_level
            df_processed = prepare_dataframe_for_inspection_level(df_raw, level)
            
            print(f"\n2. PROCESSED COLUMNS ({len(df_processed.columns)} total):")
            print("-" * 80)
            for i, col in enumerate(df_processed.columns[:30], 1):  # Show first 30
                print(f"  {i}. '{col}'")
            if len(df_processed.columns) > 30:
                print(f"  ... and {len(df_processed.columns) - 30} more columns")
            
            # Check if season/crop exist after processing
            has_season = 'season' in df_processed.columns
            has_crop = 'crop' in df_processed.columns
            
            print(f"\n3. SEASON/CROP MAPPING:")
            print("-" * 80)
            print(f"  'season' column exists: {has_season}")
            if has_season:
                season_non_null = df_processed['season'].notna().sum()
                season_null = df_processed['season'].isna().sum()
                print(f"  Season values: {season_non_null:,} non-null, {season_null:,} NULL")
                if season_non_null > 0:
                    sample_seasons = df_processed['season'].dropna().head(5).tolist()
                    print(f"  Sample season values: {sample_seasons}")
            
            print(f"  'crop' column exists: {has_crop}")
            if has_crop:
                crop_non_null = df_processed['crop'].notna().sum()
                crop_null = df_processed['crop'].isna().sum()
                print(f"  Crop values: {crop_non_null:,} non-null, {crop_null:,} NULL")
                if crop_non_null > 0:
                    sample_crops = df_processed['crop'].dropna().head(5).tolist()
                    print(f"  Sample crop values: {sample_crops}")
            
            # Check for missing mappings
            print(f"\n4. MISSING MAPPINGS:")
            print("-" * 80)
            if not has_season and season_cols_raw:
                print(f"  ERROR: Season columns found in Excel but missing after processing!")
                print(f"    Excel columns: {season_cols_raw}")
            elif not has_season:
                print(f"  WARNING: No season column found in Excel or after processing")
            
            if not has_crop and crop_cols_raw:
                print(f"  ERROR: Crop columns found in Excel but missing after processing!")
                print(f"    Excel columns: {crop_cols_raw}")
            elif not has_crop:
                print(f"  WARNING: No crop column found in Excel or after processing")
            
            if has_season and has_crop:
                print(f"  OK: Both season and crop columns are present")
            
        except Exception as e:
            print(f"\nERROR processing level {level}: {e}")
            import traceback
            traceback.print_exc()
        
        print()
    
    print("=" * 80)
    print("VALIDATION COMPLETE")
    print("=" * 80)
    
except Exception as e:
    print(f"Fatal error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
