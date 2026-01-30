"""
Reload Inspection Tables Using Existing Logic

This script reloads inspection_level_1 through inspection_level_6 tables
from an Excel file with 6 sheets, using existing logic from record_service.py
and inspection_service.py.

Requirements:
- Truncates existing data before reloading
- Uses existing business logic and validation
- Logs row counts before and after reload

Note on Cumulative Logic:
The cumulative inspection logic (s1_, s2_, etc. columns) is handled by the
optimized inspection_sheet_N tables, which are populated from inspection_level_N
tables. After running this script to reload the base tables, run the migration
script (api/v1/endpoints/cumulative/migration.py) to repopulate the optimized
tables with cumulative data.

Usage:
    python -m services.reload_inspection_tables
    # Or with custom file path:
    python -c "from services.reload_inspection_tables import reload_all_inspection_tables; reload_all_inspection_tables('path/to/file.xlsx')"
"""
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.absolute()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pandas as pd
import logging
from typing import Dict, List, Optional
from sqlalchemy import MetaData, text, inspect
from sqlalchemy.orm import Session
from core.db import SessionLocal, engine as sync_engine, DB_URL
from core.config import settings
from services.inspection_service import InspectionLevelProcessor
from services.record_service import RecordService, EnhancedRecordService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

INSPECTION_SCHEMA = "operations"
MAX_LEVEL = 6
EXCEL_FILE_PATH = r"C:\Users\madan\OneDrive\Documents\Inspection Reports Production RABI 21-25 decoded (1).xlsx"


def get_table_row_count(db: Session, table_name: str) -> int:
    """Get the current row count for a table"""
    try:
        result = db.execute(text(f"SELECT COUNT(*) FROM {INSPECTION_SCHEMA}.{table_name}"))
        count = result.scalar()
        return count or 0
    except Exception as e:
        logger.warning(f"Could not get row count for {table_name}: {e}")
        return 0


def truncate_table(db: Session, table_name: str, schema: str = INSPECTION_SCHEMA) -> None:
    """Truncate any table with explicit logging and RESTART IDENTITY"""
    full_table_name = f"{schema}.{table_name}"
    
    try:
        # Get row count BEFORE truncate
        before_count = get_table_row_count(db, table_name)
        
        logger.info("=" * 80)
        logger.info(f"TRUNCATING TABLE: {full_table_name}")
        logger.info(f"  Database: {DB_URL.get('dbname')}")
        logger.info(f"  Schema: {schema}")
        logger.info(f"  Table: {table_name}")
        logger.info(f"  Rows BEFORE truncate: {before_count:,}")
        logger.info("=" * 80)
        
        # Explicit TRUNCATE with RESTART IDENTITY
        db.execute(text(f"TRUNCATE TABLE {full_table_name} RESTART IDENTITY CASCADE"))
        db.commit()  # Explicit commit
        
        # Verify row count AFTER truncate
        after_count = get_table_row_count(db, table_name)
        
        logger.info(f"  Rows AFTER truncate: {after_count:,}")
        if after_count != 0:
            logger.error(f"  ERROR: Table still has {after_count} rows after truncate!")
            raise Exception(f"Truncate failed - table still has {after_count} rows")
        else:
            logger.info(f"  ✓ Successfully truncated {table_name} (verified: 0 rows)")
            
    except Exception as e:
        logger.error(f"✗ Failed to truncate {table_name}: {e}")
        db.rollback()
        raise


def truncate_inspection_table(db: Session, level: int) -> None:
    """Truncate an inspection level table with explicit logging and RESTART IDENTITY"""
    table_name = f"inspection_level_{level}"
    truncate_table(db, table_name)


def read_excel_sheet(file_path: str, sheet_name: str, header: int = 0) -> pd.DataFrame:
    """
    Read a specific sheet from Excel file.
    Uses existing RecordService logic for consistency.
    FORCES fresh read from disk (no caching).
    """
    try:
        logger.info("=" * 80)
        logger.info(f"READING EXCEL SHEET (FRESH FROM DISK)")
        logger.info(f"  File: {file_path}")
        logger.info(f"  Sheet: {sheet_name}")
        logger.info(f"  File exists: {os.path.exists(file_path)}")
        logger.info(f"  File size: {os.path.getsize(file_path):,} bytes")
        logger.info("=" * 80)
        
        # Clear any pandas cache to force fresh read
        if hasattr(pd.io.excel, '_xlrd'):
            pd.io.excel._xlrd = None
        if hasattr(pd.io.excel, '_openpyxl'):
            pd.io.excel._openpyxl = None
        
        # Read headers first to identify ID columns
        headers_df = pd.read_excel(
            file_path,
            sheet_name=sheet_name,
            nrows=0,
            header=header,
            engine='openpyxl'
        )
        
        # Preserve ID columns as strings (like RecordService does)
        id_columns = [col for col in headers_df.columns if "id" in str(col).lower()]
        dtype = {col: str for col in id_columns} if id_columns else None
        
        # Read full sheet - FORCE fresh read
        df = pd.read_excel(
            file_path,
            sheet_name=sheet_name,
            header=header,
            dtype=dtype,
            engine='openpyxl'
        )
        
        logger.info(f"  ✓ Read {len(df):,} rows, {len(df.columns)} columns from sheet '{sheet_name}'")
        logger.info(f"  ✓ Excel read completed - data is fresh from disk")
        
        if id_columns:
            logger.info(f"  Preserved {len(id_columns)} ID columns as strings")
        
        return df
    except Exception as e:
        logger.error(f"✗ Failed to read sheet '{sheet_name}': {e}")
        raise


def prepare_dataframe_for_inspection_level(df: pd.DataFrame, level: int) -> pd.DataFrame:
    """
    Prepare DataFrame for inspection level processing.
    Adds inspection level prefix to columns if needed.
    Since each sheet represents one inspection level, we add the prefix to all
    non-identifier columns.
    """
    import re
    df = df.copy()
    
    # DEBUG: Log original Excel headers
    logger.info("=" * 80)
    logger.info(f"DEBUG: Excel Column Detection for Level {level}")
    logger.info("=" * 80)
    logger.info(f"Original Excel columns ({len(df.columns)} total):")
    for i, col in enumerate(df.columns[:20], 1):  # Show first 20 columns
        logger.info(f"  {i}. '{col}' (type: {type(col).__name__})")
    if len(df.columns) > 20:
        logger.info(f"  ... and {len(df.columns) - 20} more columns")
    
    # First, sanitize all column names to remove special characters and normalize to lowercase
    # CRITICAL: Preserve 'season' and 'crop' column names exactly (after trimming spaces)
    sanitized_columns = {}
    detected_season_cols = []
    detected_crop_cols = []
    
    for col in df.columns:
        original_col = str(col).strip()
        # Sanitize: replace special chars with underscores and convert to lowercase
        sanitized_col = original_col.replace('/', '_').replace(' ', '_').replace('-', '_')
        sanitized_col = sanitized_col.replace('(', '').replace(')', '').replace('.', '_')
        sanitized_col = re.sub(r'_+', '_', sanitized_col).strip('_')
        sanitized_col = sanitized_col.lower()  # Normalize to lowercase for consistency
        
        # CRITICAL: Detect season columns (comprehensive search)
        if 'season' in sanitized_col and sanitized_col not in ['cropcondition', 'previouscrop']:
            detected_season_cols.append((original_col, sanitized_col))
            sanitized_col = 'season'  # Normalize to 'season'
        
        # CRITICAL: Detect crop columns (comprehensive search, exclude cropcondition, previouscrop)
        elif 'crop' in sanitized_col and sanitized_col not in ['cropcondition', 'previouscrop']:
            detected_crop_cols.append((original_col, sanitized_col))
            sanitized_col = 'crop'  # Normalize to 'crop'
        
        if original_col != sanitized_col:
            sanitized_columns[original_col] = sanitized_col
    
    # DEBUG: Log detected season/crop columns
    if detected_season_cols:
        logger.info(f"Detected SEASON columns: {detected_season_cols}")
    else:
        logger.warning("WARNING: No season column detected in Excel!")
    
    if detected_crop_cols:
        logger.info(f"Detected CROP columns: {detected_crop_cols}")
    else:
        logger.warning("WARNING: No crop column detected in Excel!")
    
    if sanitized_columns:
        df = df.rename(columns=sanitized_columns)
        logger.info(f"Sanitized {len(sanitized_columns)} column names")
        logger.info(f"Normalized column mapping: {sanitized_columns}")
    
    # Normalize column names (remove extra spaces)
    df.columns = [str(col).strip() for col in df.columns]
    
    # DEBUG: Log normalized headers
    logger.info(f"Normalized columns ({len(df.columns)} total):")
    for i, col in enumerate(df.columns[:20], 1):  # Show first 20 columns
        logger.info(f"  {i}. '{col}'")
    if len(df.columns) > 20:
        logger.info(f"  ... and {len(df.columns) - 20} more columns")
    
    # CRITICAL: Comprehensive season column detection and mapping
    # Search for any column containing "season" (case-insensitive)
    season_source_col = None
    if 'season' in df.columns:
        season_source_col = 'season'
        logger.info(f"✓ Found 'season' column with {df['season'].notna().sum()} non-null values")
    elif 'season_id' in df.columns:
        season_source_col = 'season_id'
        logger.info(f"✓ Found 'season_id' column with {df['season_id'].notna().sum()} non-null values")
    else:
        # Fallback: search for any column containing "season"
        for col in df.columns:
            col_lower = str(col).lower()
            if 'season' in col_lower and col_lower not in ['cropcondition', 'previouscrop']:
                season_source_col = col
                logger.info(f"✓ Found season-like column '{col}' with {df[col].notna().sum()} non-null values")
                break
    
    # CRITICAL: Comprehensive crop column detection and mapping
    # Search for any column containing "crop" (case-insensitive, exclude cropcondition, previouscrop)
    crop_source_col = None
    if 'crop' in df.columns:
        crop_source_col = 'crop'
        logger.info(f"✓ Found 'crop' column with {df['crop'].notna().sum()} non-null values")
    elif 'crop_id' in df.columns:
        crop_source_col = 'crop_id'
        logger.info(f"✓ Found 'crop_id' column with {df['crop_id'].notna().sum()} non-null values")
    else:
        # Fallback: search for any column containing "crop" (exclude cropcondition, previouscrop)
        for col in df.columns:
            col_lower = str(col).lower()
            if 'crop' in col_lower and col_lower not in ['cropcondition', 'previouscrop', 'crop_name']:
                crop_source_col = col
                logger.info(f"✓ Found crop-like column '{col}' with {df[col].notna().sum()} non-null values")
                break
    
    # CRITICAL: Force mapping to 'season' and 'crop' columns
    # This ensures they exist in the DataFrame even if Excel had different names
    if season_source_col:
        df['season'] = df[season_source_col]
        logger.info(f"✓ Mapped '{season_source_col}' → 'season'")
        if season_source_col != 'season':
            # Keep original column if it's different (for reference)
            logger.info(f"  Original column '{season_source_col}' preserved")
    else:
        logger.error(f"✗ ERROR: No season column found in Excel Sheet {level}!")
        logger.error(f"  Available columns: {list(df.columns)[:10]}...")
        # Create empty season column (will be NULL, but column exists)
        df['season'] = None
        logger.warning(f"  Created NULL 'season' column (will be populated from level 1 if available)")
    
    if crop_source_col:
        df['crop'] = df[crop_source_col]
        logger.info(f"✓ Mapped '{crop_source_col}' → 'crop'")
        if crop_source_col != 'crop':
            # Keep original column if it's different (for reference)
            logger.info(f"  Original column '{crop_source_col}' preserved")
    else:
        logger.error(f"✗ ERROR: No crop column found in Excel Sheet {level}!")
        logger.error(f"  Available columns: {list(df.columns)[:10]}...")
        # Create empty crop column (will be NULL, but column exists)
        df['crop'] = None
        logger.warning(f"  Created NULL 'crop' column (will be populated from level 1 if available)")
    
    logger.info("=" * 80)
    
    # Map mrno/LOT NO (sanitized as mrno_lot_no) → lot_no
    # This ensures lot_no is always populated for comparisons
    # CRITICAL: lot_no must never be NULL - it's the master key for all comparisons
    
    # Step 1: Create lot_no if it doesn't exist
    if 'lot_no' not in df.columns:
        if 'mrno_lot_no' in df.columns:
            df['lot_no'] = df['mrno_lot_no']
            logger.info("Mapped mrno_lot_no → lot_no (lot_no was missing)")
        elif 'lot_id' in df.columns:
            df['lot_no'] = df['lot_id']
            logger.info("Mapped lot_id → lot_no (lot_no was missing)")
        elif 'lot_number' in df.columns:
            df['lot_no'] = df['lot_number']
            logger.info("Mapped lot_number → lot_no (lot_no was missing)")
        else:
            logger.warning("No lot column found - lot_no will be NULL!")
    
    # Step 2: Fill empty lot_no values from alternative sources
    # CRITICAL: Normalize IMMEDIATELY after mapping, before any comparisons
    if 'lot_no' in df.columns:
        # Convert to string and normalize IMMEDIATELY
        df['lot_no'] = df['lot_no'].astype(str)
        df['lot_no'] = df['lot_no'].str.strip().str.lower()
        df['lot_no'] = df['lot_no'].replace('', None)
        df['lot_no'] = df['lot_no'].replace(['nan', 'none', 'null', 'nat', '<na>'], None)
    else:
        df['lot_no'] = None
    
    # Create mask for empty/null lot_no values (after initial normalization)
    mask = df['lot_no'].isna() | (df['lot_no'] == '')
    
    empty_count = mask.sum()
    if empty_count > 0:
        logger.warning(f"Found {empty_count} empty lot_no values after normalization, attempting to fill from alternative sources...")
        
        # Try mrno_lot_no first - normalize immediately
        if 'mrno_lot_no' in df.columns:
            temp_lot = df.loc[mask, 'mrno_lot_no'].astype(str).str.strip().str.lower()
            temp_lot = temp_lot.replace('', None).replace(['nan', 'none', 'null', 'nat', '<na>'], None)
            df.loc[mask, 'lot_no'] = temp_lot
            mask = df['lot_no'].isna() | (df['lot_no'] == '')
            filled_count = empty_count - mask.sum()
            if filled_count > 0:
                logger.info(f"Filled {filled_count} empty lot_no values from mrno_lot_no (normalized)")
        
        # Try lot_id if still empty - normalize immediately
        if mask.sum() > 0 and 'lot_id' in df.columns:
            empty_before = mask.sum()
            temp_lot = df.loc[mask, 'lot_id'].astype(str).str.strip().str.lower()
            temp_lot = temp_lot.replace('', None).replace(['nan', 'none', 'null', 'nat', '<na>'], None)
            df.loc[mask, 'lot_no'] = temp_lot
            mask = df['lot_no'].isna() | (df['lot_no'] == '')
            filled_count = empty_before - mask.sum()
            if filled_count > 0:
                logger.info(f"Filled {filled_count} empty lot_no values from lot_id (normalized)")
        
        # Try lot_number if still empty - normalize immediately
        if mask.sum() > 0 and 'lot_number' in df.columns:
            empty_before = mask.sum()
            temp_lot = df.loc[mask, 'lot_number'].astype(str).str.strip().str.lower()
            temp_lot = temp_lot.replace('', None).replace(['nan', 'none', 'null', 'nat', '<na>'], None)
            df.loc[mask, 'lot_no'] = temp_lot
            mask = df['lot_no'].isna() | (df['lot_no'] == '')
            filled_count = empty_before - mask.sum()
            if filled_count > 0:
                logger.info(f"Filled {filled_count} empty lot_no values from lot_number (normalized)")
    
    # CRITICAL: For inspection_level_1, REJECT rows with NULL lot_no (don't fill with fallback)
    # For levels 2-6, NULL is acceptable (they won't match during JOIN)
    if level == 1:
        null_lot_count = df['lot_no'].isna().sum()
        if null_lot_count > 0:
            logger.error(f"ERROR: {null_lot_count} rows have NULL lot_no in level 1 - REJECTING these rows")
            logger.error(f"  These rows will NOT be inserted (data integrity requirement)")
            # Filter out NULL lot_no rows
            df = df[df['lot_no'].notna()].copy()
            logger.info(f"  Filtered to {len(df)} rows with valid lot_no")
        
        # CRITICAL: Ensure all remaining values are normalized strings using lower(trim())
        # This normalization MUST happen before any database operations
        df['lot_no'] = df['lot_no'].astype(str).str.strip().str.lower()
        # Remove empty strings after normalization
        df['lot_no'] = df['lot_no'].replace('', None)
        # Final check - reject any rows that still have NULL lot_no after normalization
        final_null_count = df['lot_no'].isna().sum()
        if final_null_count > 0:
            logger.error(f"ERROR: {final_null_count} rows still have NULL lot_no after normalization - REJECTING")
            df = df[df['lot_no'].notna()].copy()
        logger.info(f"✓ Level 1: All {len(df)} rows have normalized lot_no (lower(trim()) applied, NULL rows rejected)")
    else:
        # For levels 2-6, normalize non-NULL values using lower(trim())
        # NULL values are acceptable (they won't match during JOIN)
        non_null_mask = df['lot_no'].notna()
        if non_null_mask.sum() > 0:
            # CRITICAL: Normalize using lower(trim()) for consistent matching
            df.loc[non_null_mask, 'lot_no'] = df.loc[non_null_mask, 'lot_no'].astype(str).str.strip().str.lower()
            # Remove empty strings after normalization
            df.loc[non_null_mask, 'lot_no'] = df.loc[non_null_mask, 'lot_no'].replace('', None)
        non_null_count = df['lot_no'].notna().sum()
        null_count = df['lot_no'].isna().sum()
        logger.info(f"✓ Level {level}: {non_null_count} non-null normalized lot_no (lower(trim()) applied), {null_count} NULL values")
    
    logger.info(f"✓ Normalized lot_no: applied lower(trim()) to all non-null values - CRITICAL for matching")
    
    # CRITICAL: Ensure season and crop columns exist and are properly formatted
    # They should already be mapped above, but verify and ensure they're never dropped
    if 'season' not in df.columns:
        logger.error("CRITICAL ERROR: 'season' column missing after mapping!")
        df['season'] = None
    
    if 'crop' not in df.columns:
        logger.error("CRITICAL ERROR: 'crop' column missing after mapping!")
        df['crop'] = None
    
    # Ensure season and crop are strings (not NULL if possible)
    # CRITICAL: Never drop these columns even if all values are NULL
    if 'season' in df.columns:
        # Convert to string, handle NaN/None values
        df['season'] = df['season'].astype(str)
        df['season'] = df['season'].replace('nan', None).replace('None', None).replace('', None)
        df['season'] = df['season'].replace('NaT', None).replace('<NA>', None)
        season_null_count = df['season'].isna().sum()
        season_non_null_count = df['season'].notna().sum()
        logger.info(f"Season column: {season_non_null_count:,} non-null, {season_null_count:,} NULL")
        if season_null_count == len(df):
            logger.warning(f"WARNING: All {len(df)} rows have NULL season values - column preserved but will be NULL")
    
    if 'crop' in df.columns:
        # Convert to string, handle NaN/None values
        df['crop'] = df['crop'].astype(str)
        df['crop'] = df['crop'].replace('nan', None).replace('None', None).replace('', None)
        df['crop'] = df['crop'].replace('NaT', None).replace('<NA>', None)
        crop_null_count = df['crop'].isna().sum()
        crop_non_null_count = df['crop'].notna().sum()
        logger.info(f"Crop column: {crop_non_null_count:,} non-null, {crop_null_count:,} NULL")
        if crop_null_count == len(df):
            logger.warning(f"WARNING: All {len(df)} rows have NULL crop values - column preserved but will be NULL")
    
    # Check if columns already have inspection_N_ prefix
    has_prefix = any(str(col).startswith(f'inspection_{level}_') for col in df.columns)
    
    if not has_prefix:
        # Add inspection level prefix to all columns except common identifier columns
        # Note: lot_no is the primary key for comparisons, lot_id is not used
        identifier_columns = [
            'season_id', 'crop_id', 'variety_id', 'grower_id', 'lot_no', 
            'lot_number', 'mrno_lot_no', 'season', 'crop', 
            'hsp_code', 'village_id', 'grower_name', 'organizer_id',
            'id', 'inspection_level', 'inspection_date', 'created_at', 'updated_at'
        ]
        
        rename_dict = {}
        for col in df.columns:
            col_str = str(col).strip().lower()
            # Skip identifier columns and columns that already have inspection prefix
            if col_str not in [ic.lower() for ic in identifier_columns] and \
               not col_str.startswith('inspection_'):
                rename_dict[col] = f'inspection_{level}_{col}'
        
        if rename_dict:
            df = df.rename(columns=rename_dict)
            logger.info(f"Added inspection_{level}_ prefix to {len(rename_dict)} columns")
        else:
            logger.info(f"No columns needed prefix addition (all are identifier columns)")
    
    return df


def process_inspection_level(
    db: Session,
    metadata: MetaData,
    df: pd.DataFrame,
    level: int,
    before_count: int
) -> int:
    """
    Process a single inspection level using existing logic.
    
    Returns:
        Number of rows inserted
    """
    try:
        logger.info(f"\n{'='*80}")
        logger.info(f"Processing Inspection Level {level}")
        logger.info(f"{'='*80}")
        logger.info(f"Rows before reload: {before_count:,}")
        logger.info(f"Rows in source data: {len(df):,}")
        
        # Prepare DataFrame
        df_prepared = prepare_dataframe_for_inspection_level(df, level)
        
        # Initialize processor
        processor = InspectionLevelProcessor(db, metadata)
        # Set engine for direct connection access in bulk_insert method
        processor.engine = sync_engine
        
        # Analyze columns - this looks for inspection_N_ pattern
        configs = processor.analyze_inspection_columns(df_prepared)
        
        if level not in configs:
            logger.info(f"No inspection columns found with pattern for level {level}, creating config from all columns")
            # Create config manually from all columns
            column_types = {}
            identifier_columns = ['season_id', 'crop_id', 'variety_id', 'grower_id', 'lot_no', 
                                'lot_number', 'mrno_lot_no', 'id', 'inspection_level', 
                                'inspection_date', 'created_at', 'updated_at']
            
            for col in df_prepared.columns:
                col_str = str(col).strip().lower()
                if col_str not in [ic.lower() for ic in identifier_columns]:
                    column_types[col] = processor._infer_column_type(df_prepared[col])
            
            from services.inspection_service import InspectionLevelConfig
            configs[level] = InspectionLevelConfig(
                level=level,
                columns=list(column_types.keys()),
                column_types=column_types,
                required_columns=[]
            )
            logger.info(f"Created manual config for level {level} with {len(column_types)} columns")
        
        # Create inspection level DataFrames
        level_dataframes = processor.create_inspection_level_dataframes(df_prepared)
        
        if level not in level_dataframes:
            logger.warning(f"No DataFrame created for level {level}")
            return 0
        
        level_df = level_dataframes[level]
        logger.info(f"Created DataFrame for level {level} with {len(level_df)} rows")
        
        # Create and setup tables
        processor.create_and_setup_tables(configs, sync_engine)
        
        # Get the table
        table_name = f"inspection_level_{level}"
        table = processor.dynamic_model_factory.created_tables.get(table_name)
        
        if table is None:
            logger.error(f"Table {table_name} not found after creation")
            return 0
        
        # Prepare records - filter to only include columns that exist in table
        # Create case-insensitive mapping
        valid_table_columns = set(table.columns.keys())
        table_columns_lower = {col.lower(): col for col in valid_table_columns}
        
        records = []
        for _, row in level_df.iterrows():
            record = row.to_dict()
            sanitized_record = processor.sanitize_record(record)
            # Filter and normalize column names to match table (case-insensitive)
            filtered_record = {}
            for key, value in sanitized_record.items():
                key_lower = key.lower()
                if key_lower in table_columns_lower:
                    # Use the exact table column name (preserves case from table definition)
                    filtered_record[table_columns_lower[key_lower]] = value
            records.append(filtered_record)
        
        # Insert records using processor's bulk_insert method which handles engine properly
        if records:
            logger.info(f"Inserting {len(records)} records into {table_name}...")
            # Use the processor's bulk_insert method which has engine support
            level_dataframes_dict = {level: level_df}
            inserted_counts = processor.bulk_insert_inspection_level_data(level_dataframes_dict)
            inserted_count = inserted_counts.get(level, 0)
            
            # EXPLICIT COMMIT after inserts
            db.commit()
            logger.info(f"✓ Successfully inserted {inserted_count:,} records")
            logger.info(f"✓ Transaction committed explicitly")
        else:
            logger.warning(f"No records to insert for level {level}")
            inserted_count = 0
        
        # Get row count after insert
        after_count = get_table_row_count(db, table_name)
        logger.info("=" * 80)
        logger.info(f"INSERTION VERIFICATION FOR {INSPECTION_SCHEMA}.{table_name}")
        logger.info(f"  Rows BEFORE insert: {before_count:,}")
        logger.info(f"  Rows INSERTED: {inserted_count:,}")
        logger.info(f"  Rows AFTER insert: {after_count:,}")
        logger.info(f"  Net change: {after_count - before_count:+,}")
        logger.info("=" * 80)
        
        if after_count != inserted_count:
            logger.warning(f"  WARNING: Row count mismatch! Expected {inserted_count:,}, got {after_count:,}")
        
        return inserted_count
        
    except Exception as e:
        logger.error(f"Error processing inspection level {level}: {e}")
        db.rollback()
        raise


def reload_all_inspection_tables(excel_file_path: str = EXCEL_FILE_PATH) -> Dict[int, Dict[str, int]]:
    """
    Main function to reload all inspection tables.
    
    Returns:
        Dict mapping level to dict with before_count, inserted_count, after_count
    """
    logger.info("=" * 80)
    logger.info("Starting Inspection Tables Reload")
    logger.info("=" * 80)
    logger.info(f"Excel file: {excel_file_path}")
    
    if not os.path.exists(excel_file_path):
        raise FileNotFoundError(f"Excel file not found: {excel_file_path}")
    
    db = SessionLocal()
    metadata = MetaData()
    results = {}
    
    try:
        # Step 0: Verify database connection and schema
        logger.info("\n" + "=" * 80)
        logger.info("DATABASE CONNECTION VERIFICATION")
        logger.info("=" * 80)
        
        # Get database name from connection
        db_info_result = db.execute(text("SELECT current_database(), current_schema()"))
        db_info = db_info_result.fetchone()
        current_db = db_info[0] if db_info else "unknown"
        current_schema = db_info[1] if db_info else "unknown"
        
        logger.info(f"  Database Name: {current_db}")
        logger.info(f"  Expected Database: {DB_URL.get('dbname')}")
        logger.info(f"  Current Schema: {current_schema}")
        logger.info(f"  Target Schema: {INSPECTION_SCHEMA}")
        logger.info(f"  Connection Host: {DB_URL.get('host')}")
        logger.info(f"  Connection Port: {DB_URL.get('port')}")
        
        if current_db != DB_URL.get('dbname'):
            logger.error(f"  ERROR: Database mismatch! Expected {DB_URL.get('dbname')}, got {current_db}")
            raise Exception(f"Database mismatch: expected {DB_URL.get('dbname')}, got {current_db}")
        
        logger.info("  ✓ Database connection verified")
        logger.info("=" * 80)
        
        # Step 1: Get row counts before truncation
        logger.info("\n" + "=" * 80)
        logger.info("Step 1: Getting current row counts...")
        logger.info("=" * 80)
        before_counts = {}
        for level in range(1, MAX_LEVEL + 1):
            table_name = f"inspection_level_{level}"
            count = get_table_row_count(db, table_name)
            before_counts[level] = count
            logger.info(f"  {table_name}: {count:,} rows")
        
        # Step 2: Hard reset - TRUNCATE ALL inspection_level tables with CASCADE
        logger.info("\n" + "=" * 80)
        logger.info("Step 2: HARD RESET - Truncating existing tables (ALL levels)...")
        logger.info("=" * 80)
        logger.info("NOTE: All tables will be reloaded with normalized lot_no values")
        logger.info("NOTE: Using TRUNCATE ... CASCADE to ensure complete cleanup")
        
        # Explicit TRUNCATE with CASCADE for each table
        for level in range(1, MAX_LEVEL + 1):
            table_name = f"inspection_level_{level}"
            full_table_name = f"{INSPECTION_SCHEMA}.{table_name}"
            
            try:
                before_count = get_table_row_count(db, table_name)
                logger.info(f"Truncating {full_table_name} (before: {before_count:,} rows)...")
                
                db.execute(text(f"TRUNCATE TABLE {full_table_name} RESTART IDENTITY CASCADE"))
                db.commit()
                
                after_count = get_table_row_count(db, table_name)
                if after_count == 0:
                    logger.info(f"✓ Successfully truncated {table_name} (verified: 0 rows)")
                else:
                    logger.error(f"✗ ERROR: {table_name} still has {after_count} rows after truncate!")
                    raise Exception(f"Truncate failed - {table_name} still has {after_count} rows")
            except Exception as e:
                logger.error(f"✗ Failed to truncate {table_name}: {e}")
                db.rollback()
                raise
        
        # Also truncate inspection_sheet tables with explicit logging
        logger.info("\n" + "=" * 80)
        logger.info("Truncating inspection_sheet tables...")
        logger.info("=" * 80)
        for level in range(1, MAX_LEVEL + 1):
            table_name = f"inspection_sheet_{level}"
            try:
                truncate_table(db, table_name)
            except Exception as e:
                # inspection_sheet tables might not exist yet, that's OK - log but don't fail
                logger.warning(f"  Could not truncate {table_name} (may not exist): {e}")
                # Verify it doesn't exist
                try:
                    count = get_table_row_count(db, table_name)
                    if count > 0:
                        logger.error(f"  ERROR: {table_name} exists and has {count} rows but truncate failed!")
                        raise
                except:
                    logger.info(f"  Table {table_name} does not exist (OK, will be created during migration)")
        
        # Step 3: Read and process each sheet
        logger.info("\n" + "=" * 80)
        logger.info("Step 3: Reading Excel sheets and processing data...")
        logger.info("=" * 80)
        
        # Get sheet names - FORCE fresh read
        try:
            # Clear any cached ExcelFile objects
            import gc
            gc.collect()
            
            # Read Excel file fresh from disk
            excel_file = pd.ExcelFile(excel_file_path, engine='openpyxl')
            sheet_names = excel_file.sheet_names
            logger.info(f"Found {len(sheet_names)} sheets: {sheet_names}")
            logger.info(f"  ✓ Excel file opened fresh from disk")
            # Close the ExcelFile to prevent caching
            excel_file.close()
        except Exception as e:
            logger.error(f"Could not read Excel file: {e}")
            raise
        
        # Process each sheet (ALL levels including level 1 - normalize all lot_no values)
        # Level 1 must also be reloaded to ensure lot_no is normalized
        for level in range(1, MAX_LEVEL + 1):
            # Try different sheet name patterns
            sheet_name = None
            for pattern in [f"Sheet{level}", f"{level}", f"Inspection {level}", f"Level {level}"]:
                if pattern in sheet_names:
                    sheet_name = pattern
                    break
            
            # If no match found, try by index (0-based)
            if sheet_name is None and level <= len(sheet_names):
                sheet_name = sheet_names[level - 1]
                logger.info(f"Using sheet at index {level - 1}: {sheet_name}")
            
            if sheet_name is None:
                logger.warning(f"No sheet found for level {level}, skipping...")
                results[level] = {
                    'before_count': before_counts[level],
                    'inserted_count': 0,
                    'after_count': 0
                }
                continue
            
            # Read sheet
            try:
                df = read_excel_sheet(excel_file_path, sheet_name)
                
                if df.empty:
                    logger.warning(f"Sheet '{sheet_name}' is empty, skipping level {level}")
                    results[level] = {
                        'before_count': before_counts[level],
                        'inserted_count': 0,
                        'after_count': 0
                    }
                    continue
                
                # Process inspection level
                try:
                    inserted_count = process_inspection_level(
                        db, metadata, df, level, before_counts[level]
                    )
                except Exception as e:
                    import traceback
                    logger.error(f"Detailed error for level {level}: {str(e)}")
                    logger.error(f"Full traceback:\n{traceback.format_exc()}")
                    raise
                
                after_count = get_table_row_count(db, f"inspection_level_{level}")
                
                results[level] = {
                    'before_count': before_counts[level],
                    'inserted_count': inserted_count,
                    'after_count': after_count
                }
                
            except Exception as e:
                logger.error(f"Failed to process level {level} from sheet '{sheet_name}': {e}")
                results[level] = {
                    'before_count': before_counts[level],
                    'inserted_count': 0,
                    'after_count': get_table_row_count(db, f"inspection_level_{level}")
                }
                continue
        
        # Step 4: Validation - Log cumulative non-null counts for s2-s6
        logger.info("\n" + "=" * 80)
        logger.info("VALIDATION: Cumulative Non-Null Counts")
        logger.info("=" * 80)
        
        for sheet_level in range(1, MAX_LEVEL + 1):
            table_name = f"inspection_sheet_{sheet_level}"
            try:
                # Check if table exists
                result = db.execute(text(f"""
                    SELECT COUNT(*) 
                    FROM information_schema.tables 
                    WHERE table_schema = '{INSPECTION_SCHEMA}' 
                    AND table_name = '{table_name}'
                """))
                table_exists = result.scalar() > 0
                
                if table_exists:
                    # Get cumulative non-null counts
                    counts_query = text(f"""
                        SELECT
                            COUNT(*) as total,
                            COUNT(s2_lot_no) as s2,
                            COUNT(s3_lot_no) as s3,
                            COUNT(s4_lot_no) as s4,
                            COUNT(s5_lot_no) as s5,
                            COUNT(s6_lot_no) as s6
                        FROM {INSPECTION_SCHEMA}.{table_name}
                    """)
                    counts_result = db.execute(counts_query)
                    counts = counts_result.fetchone()
                    
                    logger.info(f"\n{table_name}:")
                    logger.info(f"  Total rows: {counts[0]:,}")
                    logger.info(f"  s2_lot_no (non-null): {counts[1]:,}")
                    logger.info(f"  s3_lot_no (non-null): {counts[2]:,}")
                    logger.info(f"  s4_lot_no (non-null): {counts[3]:,}")
                    logger.info(f"  s5_lot_no (non-null): {counts[4]:,}")
                    logger.info(f"  s6_lot_no (non-null): {counts[5]:,}")
                else:
                    logger.info(f"\n{table_name}: Table does not exist yet (will be created by migration)")
            except Exception as e:
                logger.warning(f"Could not validate {table_name}: {e}")
        
        # Step 6: Summary
        # Step 4: Verify row counts after reload
        logger.info("\n" + "=" * 80)
        logger.info("Step 4: Verifying row counts after reload...")
        logger.info("=" * 80)
        after_counts = {}
        for level in range(1, MAX_LEVEL + 1):
            table_name = f"inspection_level_{level}"
            count = get_table_row_count(db, table_name)
            after_counts[level] = count
            before_count = before_counts.get(level, 0)
            logger.info(f"  {table_name}:")
            logger.info(f"    Before: {before_count:,} rows")
            logger.info(f"    After: {count:,} rows")
            logger.info(f"    Net change: {count - before_count:+,}")
        
        logger.info("\n" + "=" * 80)
        logger.info("Reload Summary")
        logger.info("=" * 80)
        for level in range(1, MAX_LEVEL + 1):
            result = results.get(level, {})
            logger.info(f"\nInspection Level {level}:")
            logger.info(f"  Before: {result.get('before_count', 0):,} rows")
            logger.info(f"  Inserted: {result.get('inserted_count', 0):,} rows")
            logger.info(f"  After: {result.get('after_count', 0):,} rows")
            logger.info(f"  Net Change: {result.get('after_count', 0) - result.get('before_count', 0):+,}")
        
        # Step 5: Propagate season and crop from inspection_level_1 to other levels
        logger.info("\n" + "=" * 80)
        logger.info("Step 5: Propagating season and crop from level 1 to other levels...")
        logger.info("=" * 80)
        
        # Check if level 1 has season/crop data
        level1_season_count = db.execute(text("""
            SELECT COUNT(*) FROM operations.inspection_level_1 
            WHERE season IS NOT NULL AND season != ''
        """)).scalar()
        level1_crop_count = db.execute(text("""
            SELECT COUNT(*) FROM operations.inspection_level_1 
            WHERE crop IS NOT NULL AND crop != ''
        """)).scalar()
        
        logger.info(f"Level 1 has {level1_season_count:,} non-null season values")
        logger.info(f"Level 1 has {level1_crop_count:,} non-null crop values")
        
        if level1_season_count > 0 or level1_crop_count > 0:
            # Propagate season and crop to levels 2-6 using normalized lot_no matching
            for level in range(2, MAX_LEVEL + 1):
                table_name = f"inspection_level_{level}"
                
                try:
                    # Update season
                    if level1_season_count > 0:
                        update_season_sql = text(f"""
                            UPDATE operations.{table_name} i{level}
                            SET season = i1.season
                            FROM operations.inspection_level_1 i1
                            WHERE LOWER(TRIM(CAST(i1.lot_no AS TEXT))) = LOWER(TRIM(CAST(i{level}.lot_no AS TEXT)))
                            AND i1.season IS NOT NULL AND i1.season != ''
                            AND i{level}.lot_no IS NOT NULL AND i{level}.lot_no != ''
                        """)
                        result = db.execute(update_season_sql)
                        updated_season = result.rowcount
                        db.commit()
                        logger.info(f"  Updated {updated_season:,} rows in {table_name} with season from level 1")
                    
                    # Update crop
                    if level1_crop_count > 0:
                        update_crop_sql = text(f"""
                            UPDATE operations.{table_name} i{level}
                            SET crop = i1.crop
                            FROM operations.inspection_level_1 i1
                            WHERE LOWER(TRIM(CAST(i1.lot_no AS TEXT))) = LOWER(TRIM(CAST(i{level}.lot_no AS TEXT)))
                            AND i1.crop IS NOT NULL AND i1.crop != ''
                            AND i{level}.lot_no IS NOT NULL AND i{level}.lot_no != ''
                        """)
                        result = db.execute(update_crop_sql)
                        updated_crop = result.rowcount
                        db.commit()
                        logger.info(f"  Updated {updated_crop:,} rows in {table_name} with crop from level 1")
                    
                except Exception as e:
                    logger.error(f"  ✗ Failed to propagate season/crop to {table_name}: {e}")
                    db.rollback()
        else:
            logger.warning("  WARNING: Level 1 has no season/crop data - cannot propagate to other levels")
        
        logger.info("=" * 80)
        
        logger.info("\n" + "=" * 80)
        logger.info("Reload completed successfully!")
        logger.info("=" * 80)
        
        return results
        
    except Exception as e:
        logger.error(f"Reload failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    try:
        results = reload_all_inspection_tables()
        logger.info("\nReload process completed. Results:")
        for level, result in results.items():
            logger.info(f"Level {level}: {result}")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

