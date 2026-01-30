"""
Check what columns exist in Excel file to understand season/crop mapping
"""
import sys
from pathlib import Path
import pandas as pd

# Add project root to path
project_root = Path(__file__).parent.absolute()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

EXCEL_FILE_PATH = r"C:\Users\madan\OneDrive\Documents\Inspection Reports Production RABI 21-25 decoded (1).xlsx"

print("=" * 80)
print("EXCEL COLUMN ANALYSIS")
print("=" * 80)

# Get actual sheet names
excel_file = pd.ExcelFile(EXCEL_FILE_PATH, engine='openpyxl')
sheet_names = excel_file.sheet_names
print(f"Actual sheet names: {sheet_names}\n")

for sheet_name in sheet_names:
    print(f"\n{sheet_name}:")
    print("-" * 80)
    
    try:
        df = pd.read_excel(EXCEL_FILE_PATH, sheet_name=sheet_name, nrows=5)
        
        # Check for season/crop related columns
        season_cols = [col for col in df.columns if 'season' in str(col).lower()]
        crop_cols = [col for col in df.columns if 'crop' in str(col).lower()]
        
        print(f"  Total columns: {len(df.columns)}")
        print(f"  Season-related columns: {season_cols}")
        print(f"  Crop-related columns: {crop_cols}")
        
        # Show first few column names
        print(f"  First 10 columns: {list(df.columns[:10])}")
        
    except Exception as e:
        print(f"  ERROR: {e}")

print("\n" + "=" * 80)

