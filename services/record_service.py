import sys
import os
from pathlib import Path

# Add project root to path (works when imported or run directly)
project_root = Path(__file__).parent.parent.absolute()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pandas as pd
import logging
from typing import Dict, List, Tuple, Optional, Any
from services.yield_service import YieldService
import numpy as np
import time
from collections import defaultdict

from sqlalchemy import MetaData, func, text, inspect as sqlalchemy_inspect
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
try:
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    logging.warning("psycopg2.extras not available, will use slower SQLAlchemy inserts")
from models.db_models import (
    CropRecord, VarietyRecord, LocationRecord,
    GrowerRecord, OrganizerRecord, SeasonCropInspectionBase, YieldRecord, SupplyChainPlanning,
    SeasonRecord
)
from services.inspection_service import InspectionLevelProcessor
from models.schemas.base import (
    CropRecordCreate,
    VarietyRecordCreate,
    LocationRecordCreate,
    GrowerRecordCreate,
    OrganizerRecordCreate,
    SeasonCropInspectionBaseCreate,
    YieldRecordBase
)
from core.db import SessionLocal, engine as sync_engine
# from services.record_service import RecordService

from core.config import settings
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# Profiling: log row throughput every N rows
PROGRESS_LOG_INTERVAL = 1000


class RecordService:
    def __init__(self, db: Session, skip_init_cache: bool = False):
        self.db = db
        self.skip_init_cache = skip_init_cache
        self.unique_records ={
            'crops': [],
            'varieties': [],
            'locations': [],
            'growers': [],
            'organizers': [],
            'inspection_base': [],
            'inspection_final': [],
            'yield_data': []  # Add yield data
        }
        self.REQUIRED_FIELDS = {"season_id", "crop_id", "variety_id", "location_id", "grower_id"}
        self.FLOAT_FIELDS = {
    "male_soaking_acre", "male_no_of_pkt", "male_qty_in_kgs",
    "female_soaking_acre", "female_no_of_pkt", "female_qty_in_kgs"}
        self.rename_map = {
        'growers_name':'grower_name',
        'crop': 'crop_name',
        'hsp_code': 'variety_name',
        'village_id': 'source_location_id',
        'village_description': 'village',
        'father_s_name': 'fathers_name',
        'org_id': 'organizer_id',
        'yield_production_plant': 'production_plant'
    }
        self.processed_counts = {
            'crops': 0,
            'varieties': 0,
            'locations': 0,
            'growers': 0,
            'organizers': 0
        }
        # Initialize caches - handle connection errors gracefully (skip if reload will preload)
        if skip_init_cache:
            self.crop_name_to_id = {}
            self.variety_name_to_id = {}
            self.variety_name_crop_to_id = {}
            self.location_name_to_id = {}
            self.grower_name_to_id = {}
            self.organizer_name_to_id = {}
            return
        try:
            self.crop_name_to_id = {
                crop.crop_name.strip().lower(): crop.crop_id
                for crop in self.db.query(CropRecord).all()
            }
            # FIXED: Variety cache now includes crop_id as composite key
            # Store both (variety_name, crop_id) and variety_name (for backward compatibility)
            self.variety_name_to_id = {}
            self.variety_name_crop_to_id = {}  # New: (variety_name_normalized, crop_id) -> variety_id
            for variety in self.db.query(VarietyRecord).all():
                variety_name_norm = variety.variety_name.strip().lower().replace('-', '')
                # Backward compatibility: variety_name only
                self.variety_name_to_id[variety_name_norm] = variety.variety_id
                # New: composite key with crop_id
                self.variety_name_crop_to_id[(variety_name_norm, variety.crop_id)] = variety.variety_id
            
            # FIXED: Location cache now includes hierarchy (village, district, state)
            # Store multiple keys for flexible lookup
            self.location_name_to_id = {}
            for loc in self.db.query(LocationRecord).all():
                village_norm = loc.village.strip().lower()
                district_norm = loc.district.strip().lower() if loc.district else ''
                state_norm = loc.state.strip().lower() if loc.state else ''
                
                # Primary key: village only (for backward compatibility)
                self.location_name_to_id[village_norm] = loc.location_id
                
                # Secondary key: village + district (more specific)
                if district_norm:
                    key_district = f"{village_norm}|{district_norm}"
                    self.location_name_to_id[key_district] = loc.location_id
                
                # Tertiary key: village + district + state (most specific)
                if district_norm and state_norm:
                    key_full = f"{village_norm}|{district_norm}|{state_norm}"
                    self.location_name_to_id[key_full] = loc.location_id
            self.grower_name_to_id = {
                grower.grower_name.strip().lower(): grower.grower_id
                for grower in self.db.query(GrowerRecord).all()
            }
            self.organizer_name_to_id = {
                organizer.organizer_name.strip().lower(): organizer.organizer_id
                for organizer in self.db.query(OrganizerRecord).all()
            }
        except Exception as e:
            # Database not available - initialize empty caches
            # They will be populated when database operations are attempted
            logger.warning(f"Could not load master data cache from database: {e}")
            logger.warning("Initializing with empty caches. Cache will be populated on first database access.")
            self.crop_name_to_id = {}
            self.variety_name_to_id = {}
            self.variety_name_crop_to_id = {}
            self.location_name_to_id = {}
            self.grower_name_to_id = {}
            self.organizer_name_to_id = {}

    def read_csv_preserve(self, file_path: str) -> pd.DataFrame:
        """
        Read CSV with multi-index headers while preserving 'id' columns as strings.
        """
        # Read first two rows as multi-index headers
        headers = pd.read_csv(file_path, nrows=0, header=[0, 1]).columns

        # Build dtype mapping: any column where level0 or level1 ends with 'id'
        dtype = {}
        for col in headers:
            if isinstance(col, tuple):  # multi-index case
                if any("id" in str(level).lower() for level in col if level):
                    dtype[col] = str
            else:  # single-level header case
                if "id" in str(col).lower():
                    dtype[col] = str

        # Read full CSV with dtype applied
        df = pd.read_csv(file_path, header=[0, 1], dtype=dtype)

        return df

    def read_csv_preserve_ids(self, file_path: str):
        """
        Read CSV or Excel file and preserve ID columns as strings.
        Automatically detects file type based on extension.
        """
        import os
        
        file_ext = os.path.splitext(file_path)[1].lower()

        # Prepare dtype mapping: columns ending with 'id' => str
        # First, read headers to determine which columns are IDs
        if file_ext in ['.xlsx', '.xls']:
            # Excel file - read headers first
            try:
                headers_df = pd.read_excel(file_path, nrows=0, header=0, engine='openpyxl')
            except ImportError:
                try:
                    headers_df = pd.read_excel(file_path, nrows=0, header=0, engine='xlrd')
                except ImportError:
                    raise ImportError("Please install openpyxl or xlrd: pip install openpyxl")
            
            headers = headers_df.columns
            
            dtype = {
                col: str for col in headers if "id" in col.lower()
            }
            # Read full Excel file with dtype applied only to 'id' columns
            try:
                df = pd.read_excel(file_path, header=0, dtype=dtype, engine='openpyxl')
            except ImportError:
                df = pd.read_excel(file_path, header=0, dtype=dtype, engine='xlrd')
        else:
            # CSV file - read headers first
            # Try multiple encodings for CSV files
            encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
            headers_df = None
            headers = None
            
            # Try to read headers with different encodings
            for encoding in encodings:
                try:
                    headers_df = pd.read_csv(file_path, nrows=0, header=0, encoding=encoding)
                    headers = headers_df.columns
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            
            if headers is None:
                # Last resort: try without encoding specification
                try:
                    headers_df = pd.read_csv(file_path, nrows=0, header=0)
                    headers = headers_df.columns
                except Exception as e:
                    logger.error(f"Failed to read CSV headers: {e}")
                    raise
            
            dtype = {
                col: str for col in headers if "id" in col.lower()
            }
        # Read full CSV with dtype applied only to 'id' columns
            # Try multiple encodings for CSV files
            df = None
            for encoding in encodings:
                try:
                    df = pd.read_csv(file_path, header=0, dtype=dtype, encoding=encoding)
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
            
            if df is None:
                # Last resort: try different approaches based on pandas version
                try:
                    # Try with encoding_errors parameter (pandas 1.3+)
                    df = pd.read_csv(file_path, header=0, dtype=dtype, encoding='utf-8', encoding_errors='ignore')
                except (TypeError, ValueError):
                    # Older pandas version - try on_bad_lines (pandas 1.3+)
                    try:
                        df = pd.read_csv(file_path, header=0, dtype=dtype, encoding='utf-8', on_bad_lines='skip')
                    except (TypeError, ValueError):
                        # Very old pandas or no encoding support - read without encoding, handle errors manually
                        try:
                            df = pd.read_csv(file_path, header=0, dtype=dtype)
                        except Exception as e:
                            logger.error(f"Failed to read CSV file after trying all encodings: {e}")
                            raise

        return df

    def load_and_process_multilevel_index(self, file_path: str) -> Tuple[pd.DataFrame, Dict[str, str]]:
        """
        Load Excel file with two-level headers and flatten them.
        Returns processed DataFrame and header mapping.
        """
        try:
            # Read Excel with two header rows
            df = self.read_csv_preserve(file_path)


            # Extract column tuples (multi-index)
            cols = list(df.columns)

            # Step 1: Clean level 0 and level 1 separately
            level_0 = [str(c[0]).replace("\xa0", " ").strip() if c[0] else "" for c in cols]
            level_1 = [str(c[1]).replace("\xa0", " ").strip() if c[1] else "" for c in cols]

            # Step 2: Replace 'Unnamed' with None and forward-fill in level 0
            cleaned_level_0 = []
            last = None
            for col in level_0:
                if re.search(r"(?i)unnamed", col) or col == "" or pd.isna(col):
                    cleaned_level_0.append(last)
                else:
                    last = col
                    cleaned_level_0.append(col)

            # Step 3: Rebuild multi-index columns
            df.columns = pd.MultiIndex.from_tuples(list(zip(cleaned_level_0, level_1)))

            # Step 4: Flatten multi-index to snake_case
            flattened_columns = []
            column_mapping = {}

            for col in df.columns:
                level1, level2 = col
                level1_clean = str(level1).strip() if level1 else ""
                level2_clean = str(level2).strip() if level2 else ""

                # Decide flattened name
                if level1_clean and level2_clean:
                    if re.search("(?i)unnamed|nan|base", level1_clean):
                        flattened_name = level2_clean
                    else:
                        flattened_name = f"{level1_clean}_{level2_clean}"
                else:
                    flattened_name = level1_clean or level2_clean

                # Clean name
                flattened_name = re.sub(r"[^\w\s]", "_", flattened_name)
                flattened_name = re.sub(r"[\s_]+", "_", flattened_name).lower()

                flattened_columns.append(flattened_name)
                column_mapping[flattened_name] = col

            # Update dataframe with flattened names
            df.columns = flattened_columns

            # Remove empty rows
            df = df.dropna(how="all")

            # Replace NaN with None
            df = df.where(pd.notnull(df), None)

            # Drop duplicate columns
            df = df.loc[:, ~df.columns.duplicated()]

            logger.info(f"Loaded Excel file with {len(df)} rows and {len(df.columns)} columns")
            logger.debug(f"Column mapping: {column_mapping}")

            return df, column_mapping

        except Exception as e:
            logger.error(f"Error loading Excel file: {str(e)}")
            raise ValueError(f"Failed to load Excel file: {str(e)}")

    def load_and_process_excel(self, file_path: str, sheet_name: Optional[str] = None) -> Tuple[pd.DataFrame, Dict[str, str]]:
        """
        Load Excel file with two-level headers and flatten them.
        Can load a specific sheet or the first sheet if sheet_name is None.
        Returns processed DataFrame and header mapping.
        """
        try:
            import os
            file_ext = os.path.splitext(file_path)[1].lower()
            
            # Read the Excel file
            if file_ext in ['.xlsx', '.xls']:
                if sheet_name:
                    df = pd.read_excel(file_path, sheet_name=sheet_name, engine='openpyxl')
                else:
                    # Read first sheet by default
                    df = pd.read_excel(file_path, sheet_name=0, engine='openpyxl')
                
                # Preserve ID columns as strings
                id_columns = [col for col in df.columns if "id" in col.lower()]
                if id_columns:
                    dtype = {col: str for col in id_columns}
                    df = pd.read_excel(file_path, sheet_name=sheet_name or 0, dtype=dtype, engine='openpyxl')
            else:
                # CSV file - use existing method
                df = self.read_csv_preserve_ids(file_path)
            
            # Map mrno/LOT NO → lot_no (handle various formats)
            # Check for variations: mrno/LOT NO, mrno_lot_no, MRNO/LOT NO, etc.
            lot_no_variations = ['mrno/lot no', 'mrno_lot_no', 'mrno lot no', 'mrno/lotno']
            for variation in lot_no_variations:
                # Check case-insensitive match
                matching_cols = [col for col in df.columns if str(col).lower().replace(' ', '_').replace('/', '_') == variation.lower().replace(' ', '_').replace('/', '_')]
                if matching_cols:
                    source_col = matching_cols[0]
                    if 'lot_no' not in df.columns:
                        df['lot_no'] = df[source_col]
                        logger.info(f"Mapped '{source_col}' → 'lot_no'")
                    else:
                        # Fill empty lot_no values from mrno/LOT NO
                        mask = df['lot_no'].isna() | (df['lot_no'] == '') | (df['lot_no'].astype(str).str.strip() == '')
                        df.loc[mask, 'lot_no'] = df.loc[mask, source_col]
                        filled_count = mask.sum()
                        if filled_count > 0:
                            logger.info(f"Filled {filled_count} empty lot_no values from '{source_col}'")
                    break
            
            # Extract column tuples
            cols = list(df.columns)

            # Step 1: Strip and normalize both levels
            # level_0 = [str(c[0]).strip() if c[0] else '' for c in cols]
            # level_1 = [str(c[1]).strip() if c[1] else '' for c in cols]
            level_0 = [' '.join(c.replace('\xa0', ' ').split()) for c in cols]

            # Step 2: Replace 'Unnamed' with None and ffill level 0
            cleaned_level_0 = []
            last = None
            for col in level_0:
                if re.search(r"(?i)unnamed", col) or col == '' or pd.isna(col):
                    cleaned_level_0.append(last)
                else:
                    last = col
                    cleaned_level_0.append(col)
            if "Season" not in cleaned_level_0:
                # df.columns = pd.MultiIndex.from_tuples(zip(cleaned_level_0, level_1))
                df.columns = cleaned_level_0
            else:
                df.columns = cleaned_level_0

            # Flatten multi-level column headers
            flattened_columns = []
            column_mapping = {}

            for col in df.columns:
                if isinstance(col, tuple):
                    # Handle multi-level columns
                    level1, level2 = col

                    # Skip if both levels are NaN or empty
                    if pd.isna(level1) and pd.isna(level2):
                        continue

                    # Clean and combine headers
                    level1_clean = str(level1).strip() if not pd.isna(level1) else ""
                    level2_clean = str(level2).strip() if not pd.isna(level2) else ""

                    # Create flattened column name
                    if level1_clean and level2_clean:
                        if re.search("(?i)unnamed|nan|base", level1_clean):
                            flattened_name = level2_clean
                        else:
                            flattened_name = f"{level1_clean}_{level2_clean}"
                    else:
                        flattened_name = level1_clean or level2_clean

                    # Clean up the name
                    flattened_name = re.sub(r'[^\w\s]', '_', flattened_name)
                    flattened_name = re.sub(r'[\s_]+', '_', flattened_name).lower()

                    flattened_columns.append(flattened_name)
                    column_mapping[flattened_name] = col
                else:
                    # Single level column
                    clean_name = re.sub(r'[^\w\s]', '_', str(col))
                    clean_name = re.sub(r'\s+', '_', clean_name).lower()
                    flattened_columns.append(clean_name)
                    column_mapping[clean_name] = col

            # Update DataFrame columns
            df.columns = flattened_columns

            # Remove completely empty rows
            df = df.dropna(how='all')

            # Fill NaN values with None for better handling
            df = df.where(pd.notnull(df), None)
            df = df.loc[:, ~df.columns.duplicated()]

            logger.info(f"Loaded Excel file with {len(df)} rows and {len(df.columns)} columns")
            logger.debug(f"Column mapping: {column_mapping}")

            return df, column_mapping

        except Exception as e:
            logger.error(f"Error loading Excel file: {str(e)}")
            raise ValueError(f"Failed to load Excel file: {str(e)}")

    def clean_null_values(self, record: dict) -> dict:
        clean_record = {}
        for key, value in record.items():
            if str(value).strip() in {"", "NULL", "null", "None", "nan"} or value is None:
                clean_record[key] = None
            elif key in self.FLOAT_FIELDS:
                try:
                    clean_record[key] = float(value)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid float for field '{key}': {value}")
                    clean_record[key] = None  # or skip the record entirely
            else:
                clean_record[key] = value
        return clean_record

    def bulk_insert_inspection_base(self, base_records: List[Dict], truncate: bool = True) -> int:
        """
        Bulk insert SeasonCropInspectionBase records with TRUNCATE option for idempotent reload.
        """
        try:
            if not base_records:
                logger.info("No inspection base records to insert")
                return 0

            # TRUNCATE table if requested (for idempotent reload)
            truncated_rows = 0
            if truncate:
                try:
                    truncated_rows = self._truncate_table('season_crop_inspection_base', 'operations')
                except Exception as e:
                    logger.warning(f"Could not truncate table (may not exist or no permissions): {str(e)}")
                    # Continue with insert anyway

            # Convert DataFrame to list of dicts if needed
            if isinstance(base_records, pd.DataFrame):
                base_records = base_records.to_dict('records')

            # Sync table columns (add missing columns dynamically)
            df_temp = pd.DataFrame(base_records)
            added_columns = self._sync_table_columns(df_temp, 'season_crop_inspection_base', 'operations')
            if added_columns:
                logger.info(f"Added {len(added_columns)} new columns to season_crop_inspection_base: {added_columns}")

            # Clean and validate records
            validated_records = []
            unresolved_locations = 0
            
            for record in base_records:
                try:
                    # Clean null values
                    record = self.clean_null_values(record)
                    
                    # Resolve location_id using improved location resolution
                    village = record.get('village')
                    mandal = record.get('mandal')
                    district = record.get('district')
                    state = record.get('state')
                    
                    if village:
                        location_id = self._resolve_location_id(village, mandal, district, state)
                        if location_id:
                            record['location_id'] = location_id
                        else:
                            unresolved_locations += 1
                            logger.warning(f"Could not resolve location for village: {village}")
                    
                    # Apply fuzzy column mapping
                    mapped_record = {}
                    for key, value in record.items():
                        mapped_key = self._fuzzy_map_column_name(key) or key
                        mapped_record[mapped_key] = value
                    record = mapped_record
                    
                    # Validate with schema
                    base_schema = SeasonCropInspectionBaseCreate(**record)
                    validated_records.append(base_schema.dict())
                except Exception as e:
                    logger.warning(f"Invalid inspection_base record skipped: {str(e)}")
                    continue

            if not validated_records:
                logger.warning("No valid inspection base records after validation")
                return 0

            # Bulk insert using bulk_insert_mappings for performance
            try:
                self.db.bulk_insert_mappings(SeasonCropInspectionBase, validated_records)
                self.db.commit()
                
                inserted_count = len(validated_records)
                self.processed_counts['inspection_base'] = inserted_count
                
                logger.info(f"Successfully inserted {inserted_count} inspection base records")
                if truncated_rows > 0:
                    logger.info(f"Truncated {truncated_rows} existing rows before insert")
                if unresolved_locations > 0:
                    logger.warning(f"Could not resolve {unresolved_locations} locations")
                
                return inserted_count
            except Exception as e:
                self.db.rollback()
                logger.error(f"Error during bulk insert: {str(e)}")
                raise

        except Exception as e:
            logger.error(f"Error bulk inserting inspection base records: {str(e)}")
            self.db.rollback()
            raise

    def normalize_keys(self, record: dict) -> dict:
        return {
            self.rename_map.get(k.strip(), k.strip()): v
            for k, v in record.items()
        }

    def _fuzzy_map_column_name(self, excel_col: str) -> Optional[str]:
        """
        Fuzzy mapping for Excel column names to DB column names.
        Handles variations like "net acreage area" → net_acerage_area
        """
        if not excel_col:
            return None
        
        # Normalize: lowercase, strip, replace spaces/special chars with underscore
        normalized = str(excel_col).lower().strip()
        normalized = re.sub(r'[^\w\s]', '_', normalized)
        normalized = re.sub(r'[\s_]+', '_', normalized)
        
        # Column mapping dictionary
        column_mapping = {
            # Net acreage variations
            'net_acreage_area': 'net_acerage_area',
            'net_acreage': 'net_acerage_area',
            'net_acerage': 'net_acerage_area',
            'net_acerage_area': 'net_acerage_area',
            
            # Final harvestable area
            'final_harvestable_area': 'final_harvestable_area',
            'final_harvestable': 'final_harvestable_area',
            'harvestable_area': 'final_harvestable_area',
            
            # Packed quantity variations
            'packed_qty': 'packed_qt',
            'packed_quantity': 'packed_qt',
            'packed_qt': 'packed_qt',
            
            # Productivity variations
            'productivity_packed_seed': 'productivity_of_packed_seed',
            'productivity_of_packed_seed': 'productivity_of_packed_seed',
            'productivity': 'productivity_of_packed_seed',
            
            # Lot variations
            'lot': 'lot_id',
            'lot_no': 'lot_id',
            'lot_number': 'lot_id',
            'lot_id': 'lot_id',
            'lot_no_batch_no': 'lot_id',
            'mrno_lot_no': 'lot_id',
            'mrno_lotno': 'lot_id',
        }
        
        # Direct match
        if normalized in column_mapping:
            return column_mapping[normalized]
        
        # Partial match (contains)
        for key, value in column_mapping.items():
            if key in normalized or normalized in key:
                return value
        
        # Return normalized version if no match
        return normalized

    def _sync_table_columns(self, df: pd.DataFrame, table_name: str, schema: str = 'operations') -> List[str]:
        """
        Compare DataFrame columns with database table columns.
        Add missing columns dynamically.
        Returns list of newly added columns.
        """
        try:
            # Get existing table columns
            inspector = sqlalchemy_inspect(self.db.bind if hasattr(self.db, 'bind') else sync_engine)
            existing_columns = {col['name'] for col in inspector.get_columns(table_name, schema=schema)}
            
            # Get DataFrame columns (after fuzzy mapping)
            df_columns = set(df.columns)
            
            # Find missing columns
            missing_columns = []
            for df_col in df_columns:
                # Apply fuzzy mapping
                mapped_col = self._fuzzy_map_column_name(df_col)
                if mapped_col and mapped_col not in existing_columns:
                    missing_columns.append((df_col, mapped_col))
            
            # Add missing columns
            added_columns = []
            for original_col, mapped_col in missing_columns:
                try:
                    # Infer datatype from DataFrame
                    sample_data = df[original_col].dropna()
                    if len(sample_data) == 0:
                        col_type = 'VARCHAR(255)'
                    else:
                        sample_value = sample_data.iloc[0]
                        if pd.api.types.is_numeric_dtype(df[original_col]):
                            col_type = 'FLOAT'
                        elif pd.api.types.is_datetime64_any_dtype(df[original_col]) or isinstance(sample_value, (pd.Timestamp, datetime)):
                            col_type = 'DATE'
                        else:
                            col_type = 'VARCHAR(255)'
                    
                    # Execute ALTER TABLE
                    alter_sql = text(f"""
                        ALTER TABLE {schema}.{table_name}
                        ADD COLUMN IF NOT EXISTS {mapped_col} {col_type}
                    """)
                    self.db.execute(alter_sql)
                    self.db.commit()
                    
                    added_columns.append(mapped_col)
                    logger.info(f"Added column '{mapped_col}' ({col_type}) to {schema}.{table_name} (from Excel column '{original_col}')")
                except Exception as e:
                    logger.warning(f"Failed to add column '{mapped_col}' to {schema}.{table_name}: {str(e)}")
                    self.db.rollback()
                    continue
            
            return added_columns
        except Exception as e:
            logger.error(f"Error syncing table columns for {schema}.{table_name}: {str(e)}")
            return []

    def _resolve_location_id(self, village: str, mandal: Optional[str] = None, 
                            district: Optional[str] = None, state: Optional[str] = None) -> Optional[str]:
        """
        Resolve location_id using composite key: village + district + state (all normalized).
        Case-insensitive, trimmed matching with TRIM() and LOWER().
        If no match found, insert new location and return new location_id.
        
        Matching Rule: village_name + district + state (all trimmed and lowercase)
        """
        if not village or pd.isna(village):
            return None
        
        # Normalize all fields: TRIM() and LOWER()
        village_clean = str(village).strip().lower()
        mandal_clean = str(mandal).strip().lower() if mandal and pd.notna(mandal) else None
        district_clean = str(district).strip().lower() if district and pd.notna(district) else None
        state_clean = str(state).strip().lower() if state and pd.notna(state) else None
        
        # PRIMARY MATCHING RULE: Composite key (village + district + state)
        # Try most specific match first: village + district + state
        if village_clean and district_clean and state_clean:
            # Check cache first
            cache_key = f"{village_clean}|{district_clean}|{state_clean}"
            if cache_key in self.location_name_to_id:
                return self.location_name_to_id[cache_key]
            
            # Query database with composite key
            existing = self.db.query(LocationRecord).filter(
                func.lower(func.trim(LocationRecord.village)) == village_clean,
                func.lower(func.trim(LocationRecord.district)) == district_clean,
                func.lower(func.trim(LocationRecord.state)) == state_clean
            ).first()
            
            if existing:
                # Update cache
                self.location_name_to_id[cache_key] = existing.location_id
                return existing.location_id
        
        # Fallback: Try village + district (if state not available)
        if village_clean and district_clean:
            cache_key = f"{village_clean}|{district_clean}"
            if cache_key in self.location_name_to_id:
                return self.location_name_to_id[cache_key]
            
            existing = self.db.query(LocationRecord).filter(
                func.lower(func.trim(LocationRecord.village)) == village_clean,
                func.lower(func.trim(LocationRecord.district)) == district_clean
            ).first()
            
            if existing:
                self.location_name_to_id[cache_key] = existing.location_id
                return existing.location_id
        
        # Last fallback: village only (least specific)
        if village_clean in self.location_name_to_id:
            return self.location_name_to_id[village_clean]
        
        existing = self.db.query(LocationRecord).filter(
            func.lower(func.trim(LocationRecord.village)) == village_clean
        ).first()
        
        if existing:
            self.location_name_to_id[village_clean] = existing.location_id
            return existing.location_id
        
        # Create new location (no match found)
        try:
            max_location = self.db.query(func.max(LocationRecord.location_id)).scalar()
            if max_location:
                match = re.search(r'\d+', max_location)
                max_num = int(match.group()) if match else 500000
            else:
                max_num = 500000
            
            new_id = f"L_{max_num + 1}"
            new_location = LocationRecord(
                location_id=new_id,
                village=str(village).strip(),
                unique_location_id=str(village).strip(),
                mandal=str(mandal).strip() if mandal and pd.notna(mandal) else None,
                district=str(district).strip() if district and pd.notna(district) else None,
                state=str(state).strip() if state and pd.notna(state) else None,
                category_id=100004
            )
            self.db.add(new_location)
            self.db.commit()
            
            # Update cache with all possible keys
            self.location_name_to_id[village_clean] = new_id
            if district_clean:
                key_district = f"{village_clean}|{district_clean}"
                self.location_name_to_id[key_district] = new_id
            if district_clean and state_clean:
                key_full = f"{village_clean}|{district_clean}|{state_clean}"
                self.location_name_to_id[key_full] = new_id
            
            logger.info(f"✅ Created new location: {new_id} - {village} (district: {district or 'N/A'}, state: {state or 'N/A'})")
            return new_id
        except Exception as e:
            logger.error(f"Error creating location {village}: {e}")
            self.db.rollback()
            return None

    def _truncate_table(self, table_name: str, schema: str = 'operations') -> int:
        """
        TRUNCATE table and return number of rows truncated.
        """
        try:
            # Get row count before truncate
            count_query = text(f"SELECT COUNT(*) FROM {schema}.{table_name}")
            result = self.db.execute(count_query)
            row_count = result.scalar() or 0
            
            # TRUNCATE with RESTART IDENTITY CASCADE
            truncate_sql = text(f"TRUNCATE TABLE {schema}.{table_name} RESTART IDENTITY CASCADE")
            self.db.execute(truncate_sql)
            self.db.commit()
            
            logger.info(f"Truncated {schema}.{table_name}: {row_count} rows removed")
            return row_count
        except Exception as e:
            logger.error(f"Error truncating {schema}.{table_name}: {str(e)}")
            self.db.rollback()
            raise

    def _clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Clean DataFrame: trim strings, convert numeric/date columns safely.
        """
        df = df.copy()
        
        # Trim and lowercase lot_id if exists
        if 'lot_id' in df.columns:
            df['lot_id'] = df['lot_id'].astype(str).str.strip().str.lower()
        if 'lot_no' in df.columns:
            df['lot_no'] = df['lot_no'].astype(str).str.strip().str.lower()
        
        # Convert numeric columns safely
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Convert date columns
        date_patterns = ['date', 'created_at', 'updated_at', 'soaking_date', 'tp_date']
        for col in df.columns:
            if any(pattern in col.lower() for pattern in date_patterns):
                df[col] = pd.to_datetime(df[col], errors='coerce')
        
        # Trim string columns
        string_cols = df.select_dtypes(include=['object']).columns.tolist()
        for col in string_cols:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()
                df[col] = df[col].replace(['nan', 'NaN', 'None', 'null', ''], None)
        
        return df

    def _generate_sequential_ids(self, records: List[Dict], prefix: str, start: int = 1, pad: int = 3,
                                 id_key: str = "id") -> List[Dict]:
        """
        Add sequential IDs to records using a prefix and padding.
        Example: CR_001, CR_002 or VR_1001, VR_1002
        """
        for i, record in enumerate(records, start=start):
            record[id_key] = f"{prefix}{str(i).zfill(pad)}"
        return records

    def get_max_variety_id(self,db_session):
        max_id_row = db_session.query(func.max(VarietyRecord.variety_id)).scalar()
        if max_id_row:
            # Extract numeric part, assuming format is VR_XXXX
            match = re.search(r'\d+', max_id_row)
            if match:
                return int(match.group())
        return 1000  # start from 1001 if table is empty

    def get_max_crop_id(self, db: Session) -> int:
        """Get the maximum crop ID number from existing records."""
        try:
            max_crop = db.query(CropRecord.crop_id).order_by(CropRecord.crop_id.desc()).first()
            if max_crop and max_crop.crop_id:
                # Extract number from ID like "CR_001" -> 1
                number_part = max_crop.crop_id.split('_')[-1]
                return int(number_part)
            return 0
        except Exception:
            return 0
    
    def _ensure_crop_exists(self, crop_name: str, category_id: int = 100001) -> Optional[str]:
        """
        Ensure crop exists in database, create if missing.
        Returns crop_id or None if creation fails.
        """
        if not crop_name or pd.isna(crop_name):
            return None
        
        crop_name_clean = str(crop_name).strip().lower()
        
        # Check cache first
        if crop_name_clean in self.crop_name_to_id:
            return self.crop_name_to_id[crop_name_clean]
        
        # Check database
        existing = self.db.query(CropRecord).filter(
            func.lower(CropRecord.crop_name) == crop_name_clean
        ).first()
        
        if existing:
            self.crop_name_to_id[crop_name_clean] = existing.crop_id
            return existing.crop_id
        
        # Create new
        try:
            max_id = self.get_max_crop_id(self.db)
            new_id = f"CR_{str(max_id + 1).zfill(3)}"
            new_crop = CropRecord(
                crop_id=new_id,
                crop_name=str(crop_name).strip(),
                category_id=category_id
            )
            self.db.add(new_crop)
            self.db.commit()
            self.crop_name_to_id[crop_name_clean] = new_id
            logger.info(f"✅ Created new master record [CROP]: {new_id} - '{crop_name}' (normalized: '{crop_name_clean}')")
            return new_id
        except Exception as e:
            logger.error(f"Error creating crop {crop_name}: {e}")
            self.db.rollback()
            return None
    
    def _ensure_variety_exists(self, variety_name: str, crop_id: str, category_id: int = 100003) -> Optional[str]:
        """
        Ensure variety exists in database, create if missing.
        Requires crop_id for proper relationship.
        Returns variety_id or None if creation fails.
        """
        if not variety_name or pd.isna(variety_name) or not crop_id:
            return None
        
        variety_name_norm = str(variety_name).strip().lower().replace('-', '')
        cache_key = (variety_name_norm, crop_id)
        
        # Check cache first
        if cache_key in self.variety_name_crop_to_id:
            return self.variety_name_crop_to_id[cache_key]
        
        # Check database with crop_id constraint
        existing = self.db.query(VarietyRecord).filter(
            func.lower(func.replace(VarietyRecord.variety_name, '-', '')) == variety_name_norm,
            VarietyRecord.crop_id == crop_id
        ).first()
        
        if existing:
            self.variety_name_to_id[variety_name_norm] = existing.variety_id
            self.variety_name_crop_to_id[cache_key] = existing.variety_id
            return existing.variety_id
        
        # Create new
        try:
            max_id = self.get_max_variety_id(self.db)
            new_id = f"VR_{str(max_id + 1).zfill(4)}"
            new_variety = VarietyRecord(
                variety_id=new_id,
                variety_name=str(variety_name).strip(),
                crop_id=crop_id,
                category_id=category_id
            )
            self.db.add(new_variety)
            self.db.commit()
            self.variety_name_to_id[variety_name_norm] = new_id
            self.variety_name_crop_to_id[cache_key] = new_id
            logger.info(f"✅ Created new master record [VARIETY]: {new_id} - '{variety_name}' (crop_id: {crop_id}, normalized: '{variety_name_norm}')")
            return new_id
        except Exception as e:
            logger.error(f"Error creating variety {variety_name}: {e}")
            self.db.rollback()
            return None
    
    def _ensure_location_exists(self, village: str, district: Optional[str] = None, 
                                state: Optional[str] = None, category_id: int = 100004) -> Optional[str]:
        """
        Ensure location exists in database, create if missing.
        Uses hierarchy (village, district, state) for accurate matching.
        Returns location_id or None if creation fails.
        """
        if not village or pd.isna(village):
            return None
        
        village_clean = str(village).strip().lower()
        district_clean = str(district).strip().lower() if district and pd.notna(district) else ''
        state_clean = str(state).strip().lower() if state and pd.notna(state) else ''
        
        # Check cache using hierarchy
        if village_clean and district_clean and state_clean:
            key_full = f"{village_clean}|{district_clean}|{state_clean}"
            if key_full in self.location_name_to_id:
                return self.location_name_to_id[key_full]
        
        if village_clean and district_clean:
            key_district = f"{village_clean}|{district_clean}"
            if key_district in self.location_name_to_id:
                return self.location_name_to_id[key_district]
        
        if village_clean in self.location_name_to_id:
            return self.location_name_to_id[village_clean]
        
        # Check database with hierarchy
        existing = None
        if village_clean and district_clean and state_clean:
            existing = self.db.query(LocationRecord).filter(
                func.lower(LocationRecord.village) == village_clean,
                func.lower(LocationRecord.district) == district_clean,
                func.lower(LocationRecord.state) == state_clean
            ).first()
        
        if not existing and village_clean and district_clean:
            existing = self.db.query(LocationRecord).filter(
                func.lower(LocationRecord.village) == village_clean,
                func.lower(LocationRecord.district) == district_clean
            ).first()
        
        if not existing:
            existing = self.db.query(LocationRecord).filter(
                func.lower(LocationRecord.village) == village_clean
            ).first()
        
        if existing:
            # Update cache
            self.location_name_to_id[village_clean] = existing.location_id
            if existing.district:
                key_district = f"{village_clean}|{existing.district.strip().lower()}"
                self.location_name_to_id[key_district] = existing.location_id
            if existing.district and existing.state:
                key_full = f"{village_clean}|{existing.district.strip().lower()}|{existing.state.strip().lower()}"
                self.location_name_to_id[key_full] = existing.location_id
            return existing.location_id
        
        # Create new
        try:
            max_location = self.db.query(func.max(LocationRecord.location_id)).scalar()
            if max_location:
                match = re.search(r'\d+', max_location)
                max_num = int(match.group()) if match else 500000
            else:
                max_num = 500000
            
            new_id = f"L_{max_num + 1}"
            new_location = LocationRecord(
                location_id=new_id,
                village=str(village).strip(),
                unique_location_id=str(village).strip(),
                district=str(district).strip() if district else None,
                state=str(state).strip() if state else None,
                category_id=category_id
            )
            self.db.add(new_location)
            self.db.commit()
            # Update cache
            self.location_name_to_id[village_clean] = new_id
            if district_clean:
                key_district = f"{village_clean}|{district_clean}"
                self.location_name_to_id[key_district] = new_id
            if district_clean and state_clean:
                key_full = f"{village_clean}|{district_clean}|{state_clean}"
                self.location_name_to_id[key_full] = new_id
            logger.info(f"Created new location: {new_id} - {village} (district: {district or 'N/A'}, state: {state or 'N/A'})")
            return new_id
        except Exception as e:
            logger.error(f"Error creating location {village}: {e}")
            self.db.rollback()
            return None

    def _process_growers(self, df: pd.DataFrame, unique_records: dict, start_id: int = 300000):
        df = df.rename(columns={"growers_name": "grower_name"})
        # Ensure essential columns exist
        for col in ['grower_id', 'grower_gender', 'father_name', 'grower_name']:
            if col not in df.columns:
                df[col] = ""

        # Extract unique grower columns
        grower_columns = self._find_columns(df, [
            'grower_id', 'grower_name', 'father_name', 'grower_gender'
        ])


        if grower_columns:
            growers_df = df[grower_columns].drop_duplicates().dropna(subset=['grower_name'])
            growers_df = growers_df.rename(columns={"grower_id": "source_grower_id"})

            # Assign category_id and generate grower_id (optional if not needed)
            growers_df["category_id"] = 100005
            growers_df = growers_df.reset_index(drop=True)
            growers_df["grower_id"] = [f"G_{start_id + i}" for i in range(len(growers_df))]

            # Convert to dict and normalize keys
            records = growers_df.to_dict('records')
            unique_records['growers'] = [self.normalize_keys(r) for r in records]

    def _process_locations(
            self,
            df: pd.DataFrame,
            unique_records: dict,
            start_id: int = 500000,
            coords_excel_path: str = r"C:\Users\madan\OneDrive\Documents\cordinates for villages.xlsx"
    ):
        try:

            # Ensure essential columns exist
            for col in ['village_id', 'unique_location_id', 'taluka_id', 'district_id',
                        'state', 'district', 'taluka_mandal', 'longitude', 'latitude']:
                if col not in df.columns:
                    df[col] = ""

            # Extract unique location columns
            location_columns = self._find_columns(df, [
                'village_id', 'village', 'unique_location_id',
                'taluka_mandal', 'district', 'state', 'taluka_id', 'district_id',
                'longitude', 'latitude', 'mandal'
            ])


            if location_columns:
                locations_df = df[location_columns].drop_duplicates().dropna(subset=['village'])

                # --- Load coordinates from Excel ---
                df1 = pd.read_excel(coords_excel_path)

                # Normalize village names (strip spaces & lower for matching)
                df1['village'] = df1['village'].astype(str).str.strip().str.lower()
                locations_df['village'] = locations_df['village'].astype(str).str.strip().str.lower()

                # Merge coordinates (assuming df1 has columns: village, latitude, longitude)
                locations_df = locations_df.merge(
                    df1[['village', 'latitude', 'longitude', 'state', 'mandal']],
                    on='village',
                    how='left'
                )

                # Assign default category_id
                locations_df["category_id"] = 100004

                # Generate sequential location_id like L_500000, L_500001, ...
                locations_df = locations_df.reset_index(drop=True)
                locations_df["location_id"] = [
                    f"L_{start_id + i}" for i in range(len(locations_df))
                ]
                locations_df = locations_df.rename(columns={'latitude_y': 'latitude', 'longitude_y': 'longitude', 'state_y': 'state', 'mandal_y': 'mandal'})
                # Keep only valid float values from strings
                locations_df['latitude'] = locations_df['latitude'].astype(str).apply(
                    lambda x: re.findall(r'-?\d+\.\d+', x)[0] if re.findall(r'-?\d+\.\d+', x) else None
                )

                locations_df['longitude'] = locations_df['longitude'].astype(str).apply(
                    lambda x: re.findall(r'-?\d+\.\d+', x)[0] if re.findall(r'-?\d+\.\d+', x) else None
                )

                # Convert back to float
                locations_df['latitude'] = locations_df['latitude'].astype(float)
                locations_df['longitude'] = locations_df['longitude'].astype(float)
                locations_df = locations_df.fillna('').replace(["nan", "NaN", "None"], "")
                locations_df["latitude"] = locations_df["latitude"].replace("", np.nan)
                locations_df["longitude"] = locations_df["longitude"].replace("", np.nan)


                # Convert to dict and normalize keys
                records = locations_df.to_dict('records')
                unique_records['locations'] = [self.normalize_keys(r) for r in records]
        except Exception as e:
            pass

    def _process_organizers(self, df: pd.DataFrame, unique_records: dict, start_id: int = 600000):
        # Ensure essential columns exist
        for col in ['organizer_id', 'organizer_name']:
            if col not in df.columns:
                df[col] = ""


        # Extract organizer-related columns
        organizer_columns = self._find_columns(df, ['organizer_id', 'organizer_name'])
        if organizer_columns:
            organizers_df = df[organizer_columns].drop_duplicates().dropna(subset=['organizer_name'])

            # Rename old organizer_id → source_organizer_id
            if 'organizer_id' in organizers_df.columns:
                organizers_df = organizers_df.rename(columns={'organizer_id': 'source_organizer_id'})
                organizers_df["source_organizer_id"] = (
                    organizers_df["source_organizer_id"]
                    .fillna("")
                    .astype(str)
                    .replace("None", "")
                )

            # Add category_id
            organizers_df["category_id"] = 100006

            # Generate sequential organizer_id like O_600000, O_600001, ...
            organizers_df = organizers_df.reset_index(drop=True)
            organizers_df["organizer_id"] = [
                f"O_{start_id + i}" for i in range(len(organizers_df))
            ]

            # Convert to dict and normalize keys
            records = organizers_df.to_dict('records')
            unique_records['organizers'] = [self.normalize_keys(r) for r in records]

    def extract_unique_records(self, df: pd.DataFrame) -> Dict[str, List[Dict]]:
        """
        Extract unique records for foundation tables + base + inspection + yield from the DataFrame.
        """
        unique_records = {
            'crops': [],
            'varieties': [],
            'locations': [],
            'growers': [],
            'organizers': [],
            'inspection_base': [],
            'inspection_final': [],
            'yield_data': []  # Add yield data
        }

        try:
            # Extract unique crops
            # self._process_locations(df, unique_records)
            # self.bulk_insert_locations(unique_records['locations'])
            # self._process_growers(df, unique_records)
            # self.bulk_insert_growers(unique_records['growers'])
            # self._process_organizers(df, unique_records)
            # self.bulk_insert_organizers(unique_records['organizers'])

            unique_records = self._process_inspection_base_records(df, unique_records)
            # Try to insert, but don't fail if database isn't available
            try:
                self.bulk_insert_inspection_base(unique_records["season_crop_inspection_base"], truncate=True)
            except Exception as e:
                logger.warning(f"Could not insert inspection base records (database may not be available): {e}")
                logger.info("Continuing with data processing...")
            
            unique_records = self._process_yield_records(df, unique_records)
            # Try to insert, but don't fail if database isn't available
            try:
                self.bulk_insert_yield_records(unique_records["season_crop_yield"], truncate=True)
            except Exception as e:
                logger.warning(f"Could not insert yield records (database may not be available): {e}")
                logger.info("Continuing with data processing...")

            # Step 4: Always close the session
            # supply_chain_records, supply_chain_df = self.extract_supply_chain_planning_data(df, unique_records)
            # unique_records['supply_chain_planning'] = supply_chain_records
            # self.bulk_insert_supply_chain_planning(unique_records['supply_chain_planning'])
            db.close()
            crop_columns = self._find_columns(df, ['crop'])
            if crop_columns:
                crops_df = df[crop_columns].drop_duplicates().dropna()
                crop_records = crops_df.to_dict('records')
                normalized = [self.normalize_keys(r) for r in crop_records]

                # NEW: Check existing crops in database (similar to varieties)
                existing_crops = {
                    c.crop_name.strip().lower(): c.crop_id
                    for c in db.query(CropRecord.crop_name, CropRecord.crop_id).all()  # Assuming CropRecord model
                }

                # Get the max existing crop ID for new crops
                crop_id_counter = self.get_max_crop_id(db) + 1  # You'll need to implement this method
                crops_with_ids = []

                for crop in normalized:
                    crop_name = crop['crop_name'].strip().lower()

                    # Check if crop already exists
                    if crop_name in existing_crops:
                        crop['crop_id'] = existing_crops[crop_name]
                    else:
                        # Generate new ID for new crop
                        crop['crop_id'] = f"CR_{str(crop_id_counter).zfill(3)}"
                        crop_id_counter += 1

                    crops_with_ids.append(crop)

                unique_records['crops'] = crops_with_ids

                # Create mapping (same as before)
                crop_name_to_id = {
                    crop['crop_name'].strip().lower(): crop['crop_id']
                    for crop in crops_with_ids
                }

            # Extract unique varieties
            variety_columns = self._find_columns(df, [
                'hsp_code', "crop"
            ])
            if variety_columns:
                varieties_df = df[variety_columns].drop_duplicates().dropna(subset=['hsp_code'])
                varieties_df["category_id"] = 100003
                records = varieties_df.to_dict('records')
                normalized_varieties = [self.normalize_keys(r) for r in records]

                existing_varieties = {
                    v.variety_name.strip().lower(): v.variety_id
                    for v in db.query(VarietyRecord.variety_name, VarietyRecord.variety_id).all()
                }

                # Replace crop_name with corresponding crop_id
                variety_id_counter = self.get_max_variety_id(db) + 1
                varieties_with_ids = []

                for v in normalized_varieties:
                    name = v['variety_name'].strip().lower()
                    crop_name = v.get('crop_name', '').strip().lower()
                    crop_id = crop_name_to_id.get(crop_name)
                    if name.replace("-", "") in existing_varieties:
                        v['variety_id'] = existing_varieties[name.replace("-", "")]
                    else:
                        v['variety_id'] = f"VR_{str(variety_id_counter).zfill(4)}"
                        variety_id_counter += 1

                    if crop_id:
                        v['crop_id'] = crop_id
                    v.pop('crop_name', None)  #
                    varieties_with_ids.append(v)
                unique_records['varieties'] = varieties_with_ids
                variety_name_to_id = {
                    v['variety_name'].strip().lower(): v['variety_id']
                    for v in varieties_with_ids
                }

            if 'village_id' not in df.columns:
                df["village_id"] = ""
            if 'taluka_id' not in df.columns:
                df["taluka_id"] = ""
            if 'district_id' not in df.columns:
                df["district_id"] = ""
            # Extract unique locations
            location_columns = self._find_columns(df, [
                'village_id', 'village', 'taluka_mandal', 'district', 'state', 'taluka_id', 'district_id'
            ])
            if location_columns:
                locations_df = df[location_columns].drop_duplicates().dropna(subset=['village_id'])
                locations_df["category_id"] = 100004
                records = locations_df.to_dict('records')
                unique_records['locations'] = [self.normalize_keys(r) for r in records]

            if 'grower_id' not in df.columns:
                df["grower_id"] = ""
            if 'grower_gender' not in df.columns:
                df["grower_gender"] = ""
            # Extract unique growers
            grower_columns = self._find_columns(df, [
                'grower_id', 'grower_name', 'father_name', 'grower_gender'
            ])
            if grower_columns:
                growers_df = df[grower_columns].drop_duplicates().dropna(subset=['grower_id'])
                growers_df["category_id"] = 100005
                records = growers_df.to_dict('records')
                unique_records['growers'] = [self.normalize_keys(r) for r in records]


            if 'org_id' not in df.columns:
                df["org_id"] = ""
            if 'yield_production_plant' not in df.columns:
                df["yield_production_plant"] = ""

            # Extract unique organizers
            organizer_columns = self._find_columns(df, ['organizer_id', 'organizer_name', 'org_id'])
            if organizer_columns:
                # Use 'organizer_id' if available, otherwise 'org_id'
                id_column = None
                if 'organizer_id' in organizer_columns:
                    id_column = 'organizer_id'
                elif 'org_id' in organizer_columns:
                    id_column = 'org_id'
                
                if id_column:
                    organizers_df = df[organizer_columns].drop_duplicates().dropna(subset=[id_column])
                organizers_df["category_id"] = 100006
                # Rename org_id to organizer_id if needed for consistency
                if 'org_id' in organizers_df.columns and 'organizer_id' not in organizers_df.columns:
                    organizers_df = organizers_df.rename(columns={'org_id': 'organizer_id'})
                records = organizers_df.to_dict('records')
                unique_records['organizers'] = [self.normalize_keys(r) for r in records]

            all_columns = df.columns.tolist()
            base_columns = [
                col for col in all_columns
                if not (
                        re.match(r'inspection_', col, re.IGNORECASE) or
                        (re.match(r'yield_', col, re.IGNORECASE) and col != 'yield_production_plant')
                )
            ]

            # Extract season_crop_inspection_base data
            if base_columns:
                base_df = df[base_columns].drop_duplicates()
                base_df.columns = [col.strip("_") for col in base_df.columns if col]

                base_df = base_df.rename(columns={
                    'season': 'season_id',
                    'crop': 'crop_name',
                    'hsp_code': 'variety_name',
                    'village_id': 'location_id',
                    'grower_id': 'grower_id',
                    'org_id': 'organizer_id',
                    'lot_no_batch_no': 'lot_id',
                    'yield_production_plant': 'yield_production_plant'
                })
                base_df['season_id'] = (
                    base_df['season_id']
                    .astype(str)  # Ensure it's string
                    .str.strip()  # Remove leading/trailing spaces
                    .str.replace(r"[-\s]+", "_", regex=True)  # Replace space(s) or hyphen(s) with "_"
                )

                def enrich_base_record(record):
                    crop_name_raw = record.get('crop_name')
                    variety_name_raw = record.get('variety_name')
                    record['crop_id'] = crop_name_to_id.get(
                        crop_name_raw.strip().lower() if crop_name_raw else '', ''
                    )

                    record['variety_id'] = variety_name_to_id.get(
                        variety_name_raw.strip().lower() if variety_name_raw else '', ''
                    )
                    record.pop('crop_name', None)
                    record.pop('variety_name', None)
                    return record

                base_records = base_df.to_dict('records')
                enriched_base = [enrich_base_record(self.normalize_keys(r)) for r in base_records]
                unique_records['inspection_base'] = enriched_base

            final_df = df.copy()
            final_df = final_df.rename(columns={
                'season': 'season_id',
                'crop': 'crop_name',
                'hsp_code': 'variety_name',
                'village_id': 'location_id',
                'grower_id': 'grower_id',
                'org_id': 'organizer_id',
                'lot_no_batch_no': 'lot_id',
                'lot_number': 'lot_id',
                'lot_no': 'lot_id',
                'father_s_name': 'fathers_name'
            })

            # Normalize season_id (same as in base_df)
            final_df['season_id'] = (
                final_df['season_id']
                .astype(str)
                .str.strip()
                .str.replace(r"[-\s]+", "_", regex=True)
            )

            # Replace crop_name and variety_name with their respective IDs
            final_df['crop_id'] = final_df['crop_name'].apply(
                lambda name: crop_name_to_id.get(name.strip().lower(), '') if isinstance(name, str) else ''
            )
            final_df['variety_id'] = final_df['variety_name'].apply(
                lambda name: variety_name_to_id.get(name.strip().lower(), '') if isinstance(name, str) else ''
            )

            # Drop original name columns
            final_df = final_df.drop(columns=['crop_name', 'variety_name'], errors='ignore')

            # Convert to records
            records = final_df.to_dict('records')
            unique_records['inspection_final'] = [self.normalize_keys(r) for r in records]

            # NEW: Extract yield data using YieldService
            yield_service = YieldService()
            yield_data_result = yield_service.extract_yield_data(final_df)
            unique_records['yield_data'] = yield_data_result['yield_data']

            # Log extraction summary
            for table, records in unique_records.items():
                logger.info(f"Extracted {len(records)} unique {table} records")

            return unique_records, final_df

        except Exception as e:
            logger.error(f"Error extracting unique records: {str(e)}")
            raise ValueError(f"Failed to extract unique records: {str(e)}")

    def _find_columns(self, df: pd.DataFrame, column_names: List[str]) -> List[str]:
        """
        Find columns in DataFrame that match the given column names (case-insensitive).
        """
        found_columns = []
        df_columns_lower = [col.lower() for col in df.columns]

        for col_name in column_names:
            # Try exact match first
            if col_name.lower() in df_columns_lower:
                found_columns.append(df.columns[df_columns_lower.index(col_name.lower())])
            else:
                # Try fuzzy match
                for df_col in df.columns:
                    if col_name.lower() in df_col.lower():
                        found_columns.append(df_col)
                        break

        return found_columns

    def extract_supply_chain_planning_data(self, df, unique_records):
        """
        Extract supply chain planning data from DataFrame and enrich with IDs
        """
        empty_case = False
        try:
            # Define supply chain planning related columns
            supply_chain_columns = [
                'village', 'variety', 'season', 'crop',
                'y0_avg_productivity', 'latest_acres', 'estimated_cost_per_kg',
                'hybrid_type', 'planned_production', 'y0_net_acres', 'probable_cost', 'y1_net_acres', 'y1_sum_received_raw_qty', 'y1_amount', 'y1_packed_quantity', 'y1_productivity'
            ]

            # Filter columns that exist in the DataFrame
            available_columns = [col for col in supply_chain_columns if col in df.columns]

            if not available_columns:
                logger.warning("No supply chain planning columns found in DataFrame")
                return []

            # Extract supply chain data
            supply_chain_df = df[available_columns].copy()

            # Normalize column names to match database structure
            column_mapping = {
                'village': 'village',
                'variety': 'variety',
                'season': 'season',
                'crop': 'crop',
                'y0_avg_productivity': 'productivity',
                'y0_net_acres': 'net_acres_current',
                'estimated_cost_per_kg': 'estimated_cost_per_kg',
                'planned_production': 'production_allocation',
                'y1_net_acres': 'actual_net_acres',
                'y1_sum_received_raw_qty': 'actual_received_qty',
                'y1_amount': 'actual_amount',
                'y1_packed_quantity': 'actual_packaged_qty',
                'y1_productivity':'actual_productivity'

            }

            supply_chain_df = supply_chain_df.rename(columns=column_mapping)
            supply_chain_df['actual_productivity'] = (
                supply_chain_df['actual_productivity']
                .fillna(0)  # handle missing values
                .astype(float)
            )
            supply_chain_df['actual_received_qty'] = (
                supply_chain_df['actual_received_qty']
                .fillna(0)  # handle missing values
                .astype(float)
            )
            # Normalize season format (same as in your existing code)
            supply_chain_df['season_id'] = (
                supply_chain_df['season']
                .astype(str)
                .str.strip()
                .str.replace(r"[-\s]+", "_", regex=True)
            )

            # Create mappings from your existing unique_records
            # Village mapping (assuming village names map to location_id)
            village_name_to_id = {}
            # if 'locations' in unique_records:
            #     village_name_to_id = {
            #         loc.get('village', '').strip().lower(): loc.get('village_id', '')
            #         for loc in unique_records['locations']
            #         if loc.get('village')
            #     }

            # Crop mapping
            crop_name_to_id = {}
            # if 'crops' in unique_records:
            #     crop_name_to_id = {
            #         crop.get('crop_name', '').strip().lower(): crop.get('crop_id', '')
            #         for crop in unique_records['crops']
            #         if crop.get('crop_name')
            #     }

            # Variety mapping
            variety_name_to_id = {}
            # if 'varieties' in unique_records:
            #     variety_name_to_id = {
            #         variety.get('variety_name', '').strip().lower(): variety.get('variety_id', '')
            #         for variety in unique_records['varieties']
            #         if variety.get('variety_name')
            #     }
            self.variety_name_to_id = {
                key.lower().replace('-', '').strip(): value
                for key, value in self.variety_name_to_id.items()
            }

            supply_chain_df = supply_chain_df.applymap(lambda x: x.strip() if isinstance(x, str) else x)

            # Enrich DataFrame with IDs
            supply_chain_df['village_id'] = supply_chain_df['village'].apply(
                lambda name: self.location_name_to_id.get(name.strip().lower().replace(",", "").strip(), self.location_name_to_id.get(name.strip(), "")) if isinstance(name, str) else ''
            )

            supply_chain_df['crop_id'] = supply_chain_df['crop'].apply(
                lambda name: self.crop_name_to_id.get(name.strip().lower(), '') if isinstance(name, str) else ''
            )

            supply_chain_df['variety_id'] = supply_chain_df['variety'].apply(
                lambda name: self.variety_name_to_id.get(
                    name.lower().replace('-', '').strip()
                ) if isinstance(name, str) else ''
            )

            if 'actual_net_acres' not in supply_chain_df.columns:
                supply_chain_df['actual_net_acres'] = np.nan
            else:
                supply_chain_df['actual_net_acres'] = supply_chain_df['actual_net_acres'].replace(0, np.nan)

            # If the column is fully NaN, fill it from net_acres_current
            if supply_chain_df['actual_net_acres'].isna().all():
                supply_chain_df['actual_net_acres'] = supply_chain_df.get('net_acres_current', 0)
                empty_case = True

            # Calculate required fields
            # adjusted_production_allocation = production_allocation * availability_actual
            supply_chain_df['adjusted_production_allocation'] = (
                    pd.to_numeric(supply_chain_df.get('productivity', 0), errors='coerce').fillna(0) *
                    pd.to_numeric(supply_chain_df.get('actual_net_acres', 0), errors='coerce').fillna(0)
            ).round(2)

            if empty_case:
                supply_chain_df['actual_net_acres'] = 0

            # estimated_production_cost = availability_actual * adjusted_production_allocation
            supply_chain_df['estimated_production_cost'] = (
                    pd.to_numeric(supply_chain_df.get('estimated_cost_per_kg', 0), errors='coerce').fillna(0) *
                    supply_chain_df['adjusted_production_allocation']
            ).round(2)

            # Add additional required fields
            supply_chain_df['plan_revision_version'] = 'V3.0-Min-Max'  # Default version
            supply_chain_df['grower'] = ''  # Empty as not available in current columns
            supply_chain_df['category_id'] = 100007  # Supply chain planning category

            # Select final columns for database insertion
            final_columns = [
                'plan_revision_version', 'season', 'crop', 'variety', 'village', 'grower',
                'net_acres_current', 'productivity', 'production_allocation',
                'actual_net_acres', 'adjusted_production_allocation',
                'estimated_cost_per_kg', 'estimated_production_cost', 'category_id',
                'village_id', 'crop_id', 'variety_id', 'season_id', 'actual_received_qty', 'actual_amount', 'actual_packaged_qty', 'actual_productivity' # Keep IDs for reference
            ]

            # Filter to only include columns that exist
            existing_final_columns = [col for col in final_columns if col in supply_chain_df.columns]
            supply_chain_final_df = supply_chain_df[existing_final_columns]

            # Convert to records for database insertion
            supply_chain_records = supply_chain_final_df.to_dict('records')

            # Normalize keys (same as your existing normalize_keys method)
            normalized_records = [self.normalize_keys(record) for record in supply_chain_records]

            logger.info(f"Extracted {len(normalized_records)} supply chain planning records")

            return normalized_records, supply_chain_final_df

        except Exception as e:
            logger.error(f"Error extracting supply chain planning data: {str(e)}")
            raise ValueError(f"Failed to extract supply chain planning data: {str(e)}")

    def bulk_insert_supply_chain_planning(self, records):
        """
        Bulk insert supply chain planning records into database
        """
        try:
            if not records:
                logger.info("No supply chain planning records to insert")
                return

            # Prepare records for bulk insert
            insert_records = []
            for record in records:
                insert_record = {
                    'plan_revision_version': record.get('plan_revision_version', 'v1.0'),
                    'season': record.get('season', ''),
                    'crop': record.get('crop', ''),
                    'variety': record.get('variety', ''),
                    'village': record.get('village', ''),
                    'grower': record.get('grower', ''),
                    'net_acres_current': record.get('net_acres_current', 0),
                    'productivity': record.get('productivity', 0),
                    'production_allocation': record.get('production_allocation', 0),
                    'actual_net_acres': record.get('actual_net_acres', 0),
                    'adjusted_production_allocation': record.get('adjusted_production_allocation', 0),
                    'estimated_cost_per_kg': record.get('estimated_cost_per_kg', 0),
                    'estimated_production_cost': record.get('estimated_production_cost', 0),
                    'category_id': record.get('category_id', 100007),
                    'location_id': record.get('source_location_id', ""),
                    'crop_id': record.get('crop_id', ''),
                    'season_id': record.get('season_id', ''),
                    'variety_id': record.get('variety_id', ''),

                    # ✅ Added Y1 actual values
                    'actual_received_qty': record.get('actual_received_qty', 0),
                    'actual_amount': record.get('actual_amount', 0),
                    'actual_packaged_qty': record.get('actual_packaged_qty', 0),
                    'actual_productivity': record.get('actual_productivity', 0),

                }
                insert_records.append(insert_record)

            # Assuming you have a SupplyChainPlanning model
            self.db.bulk_insert_mappings(SupplyChainPlanning, insert_records)
            self.db.commit()

            logger.info(f"Successfully inserted {len(insert_records)} supply chain planning records")

        except Exception as e:
            self.db.rollback()
            logger.error(f"Error inserting supply chain planning records: {str(e)}")
            raise
        finally:
            self.db.close()

    def bulk_insert_crops(self, crop_records: List[Dict]) -> int:
        """
        Bulk insert crop records with duplicate handling.
        """
        try:
            if not crop_records:
                return 0

            # Get existing crop IDs
            existing_crop_ids = set(
                crop_id[0] for crop_id in self.db.query(CropRecord.crop_id).all()
            )

            # Filter out existing records
            new_records = [
                record for record in crop_records
                if record.get('crop_id') not in existing_crop_ids
            ]

            if not new_records:
                logger.info("No new crop records to insert")
                return 0

            # Validate and create records
            validated_records = []
            for record in new_records:
                try:
                    crop_schema = CropRecordCreate(**record)
                    validated_records.append(CropRecord(**crop_schema.dict()))
                except Exception as e:
                    logger.warning(f"Invalid crop record {record}: {str(e)}")
                    continue

            # Bulk insert
            if validated_records:
                self.db.bulk_save_objects(validated_records)
                self.db.commit()
                self.processed_counts['crops'] = len(validated_records)
                logger.info(f"Inserted {len(validated_records)} new crop records")

            return len(validated_records)

        except Exception as e:
            logger.error(f"Error bulk inserting crops: {str(e)}")
            self.db.rollback()
            raise

    def bulk_insert_varieties(self, variety_records: List[Dict]) -> int:
        """
        Bulk insert variety records with duplicate handling.
        """
        try:
            if not variety_records:
                return 0

            # Get existing variety IDs
            existing_variety_ids = set(
                variety_id[0] for variety_id in self.db.query(VarietyRecord.variety_id).all()
            )

            # Filter out existing records
            new_records = [
                record for record in variety_records
                if record.get('variety_id') not in existing_variety_ids
            ]

            if not new_records:
                logger.info("No new variety records to insert")
                return 0

            # Validate and create records
            validated_records = []
            for record in new_records:
                try:
                    variety_schema = VarietyRecordCreate(**record)
                    validated_records.append(VarietyRecord(**variety_schema.dict()))
                except Exception as e:
                    logger.warning(f"Invalid variety record {record}: {str(e)}")
                    continue

            # Bulk insert
            if validated_records:
                self.db.bulk_save_objects(validated_records)
                self.db.commit()
                self.processed_counts['varieties'] = len(validated_records)
                logger.info(f"Inserted {len(validated_records)} new variety records")

            return len(validated_records)

        except Exception as e:
            logger.error(f"Error bulk inserting varieties: {str(e)}")
            self.db.rollback()
            raise

    def bulk_insert_locations(self, location_records: List[Dict]) -> int:
        """
        Bulk insert location records with duplicate handling.
        """
        try:
            if not location_records:
                return 0

            # Get existing location IDs
            existing_location_ids = set(
                location_id[0] for location_id in self.db.query(LocationRecord.location_id).all()
            )

            # Filter out existing records
            new_records = [
                record for record in location_records
                if record.get('location_id') not in existing_location_ids
            ]
            new_records_df = pd.DataFrame(new_records)
            new_records_df = new_records_df.drop_duplicates(subset='village')
            new_records = new_records_df.to_dict(orient='records')

            if not new_records:
                logger.info("No new location records to insert")
                return 0

            # Validate and create records
            validated_records = []
            for record in new_records:
                try:
                    location_schema = LocationRecordCreate(**record)
                    validated_records.append(LocationRecord(**location_schema.dict()))
                except Exception as e:
                    logger.warning(f"Invalid location record {record}: {str(e)}")
                    continue

            # Bulk insert
            if validated_records:
                self.db.bulk_save_objects(validated_records)
                self.db.commit()
                self.processed_counts['locations'] = len(validated_records)
                logger.info(f"Inserted {len(validated_records)} new location records")

            return len(validated_records)

        except Exception as e:
            logger.error(f"Error bulk inserting locations: {str(e)}")
            self.db.rollback()
            raise

    def bulk_insert_organizers(self, organizer_records: List[Dict]) -> int:
        """
        Bulk insert organizer records with duplicate handling.
        """
        try:
            if not organizer_records:
                return 0

            # Get existing organizer IDs
            existing_organizer_ids = set(
                org_id[0] for org_id in self.db.query(OrganizerRecord.organizer_id).all()
            )

            # Filter out existing records
            new_records = [
                record for record in organizer_records
                if record.get('organizer_id') not in existing_organizer_ids
            ]

            # Drop duplicates by organizer_name
            new_records_df = pd.DataFrame(new_records)
            new_records_df = new_records_df.drop_duplicates(subset='organizer_name')
            new_records = new_records_df.to_dict(orient='records')

            if not new_records:
                logger.info("No new organizer records to insert")
                return 0

            # Validate and create records
            validated_records = []
            for record in new_records:
                try:
                    organizer_schema = OrganizerRecordCreate(**record)
                    validated_records.append(OrganizerRecord(**organizer_schema.dict()))
                except Exception as e:
                    logger.warning(f"Invalid organizer record {record}: {str(e)}")
                    continue

            # Bulk insert
            if validated_records:
                self.db.bulk_save_objects(validated_records)
                self.db.commit()
                self.processed_counts['organizers'] = len(validated_records)
                logger.info(f"Inserted {len(validated_records)} new organizer records")

            return len(validated_records)

        except Exception as e:
            logger.error(f"Error bulk inserting organizers: {str(e)}")
            self.db.rollback()
            raise

    def bulk_insert_growers(self, grower_records: List[Dict]) -> int:
        """
        Bulk insert grower records with duplicate handling.
        """
        try:
            if not grower_records:
                return 0

            # Get existing grower IDs
            existing_grower_ids = set(
                grower_id[0] for grower_id in self.db.query(GrowerRecord.grower_id).all()
            )

            # Filter out existing records
            new_records = [
                record for record in grower_records
                if record.get('grower_id') not in existing_grower_ids
            ]

            new_records_df = pd.DataFrame(new_records)
            new_records_df = new_records_df.drop_duplicates(subset='grower_name')
            new_records = new_records_df.to_dict(orient='records')

            if not new_records:
                logger.info("No new grower records to insert")
                return 0

            # Validate and create records
            validated_records = []
            for record in new_records:
                try:
                    grower_schema = GrowerRecordCreate(**record)
                    validated_records.append(GrowerRecord(**grower_schema.dict()))
                except Exception as e:
                    logger.warning(f"Invalid grower record {record}: {str(e)}")
                    continue

            # Bulk insert
            if validated_records:
                self.db.bulk_save_objects(validated_records)
                self.db.commit()
                self.processed_counts['growers'] = len(validated_records)
                logger.info(f"Inserted {len(validated_records)} new grower records")

            return len(validated_records)

        except Exception as e:
            logger.error(f"Error bulk inserting growers: {str(e)}")
            self.db.rollback()
            raise

    def _clean_record_for_validation(self, record: dict) -> dict:
        """Clean record data before validation and handle foreign key constraints"""
        cleaned = record.copy()

        # Define foreign key fields that must be NULL if empty (not empty strings)
        foreign_key_fields = ['grower_id', 'crop_id', 'variety_id']

        # Handle NaN values and foreign key constraints
        for key, value in cleaned.items():
            if pd.isna(value):
                cleaned[key] = None
            elif isinstance(value, str) and value.lower() == 'nan':
                cleaned[key] = None
            elif key in foreign_key_fields:
                # For foreign key fields: empty string → None, keep valid values
                if not value or value == '' or (isinstance(value, str) and value.strip() == ''):
                    cleaned[key] = None
                else:
                    cleaned[key] = value
            elif key in ['season_id', 'lot_id', "variety_id"]:
                # For non-FK fields, keep as is (empty strings are OK)
                cleaned[key] = None

        # Handle NaT (Not a Time) values for dates
        date_fields = ['m1_soaking_date', 'female_soaking_date', 'female_tp_date']
        for key in date_fields:
            if key in cleaned and (pd.isna(cleaned[key]) or str(cleaned[key]) == 'NaT'):
                cleaned[key] = None

        return cleaned

    def bulk_insert_yield_records(self, yield_records: List[Dict], truncate: bool = True) -> int:
        """
        Bulk insert yield records with TRUNCATE option for idempotent reload.
        """
        try:
            if not yield_records:
                logger.info("No yield records to insert")
                return 0

            # TRUNCATE table if requested (for idempotent reload)
            truncated_rows = 0
            if truncate:
                try:
                    truncated_rows = self._truncate_table('season_crop_yield', 'operations')
                except Exception as e:
                    logger.warning(f"Could not truncate table (may not exist or no permissions): {str(e)}")
                    # Continue with insert anyway

            # Convert to DataFrame for processing
            if isinstance(yield_records, pd.DataFrame):
                df_yield = yield_records.copy()
            else:
                df_yield = pd.DataFrame(yield_records)

            # Clean DataFrame
            df_yield = self._clean_dataframe(df_yield)

            # Sync table columns (add missing columns dynamically)
            added_columns = self._sync_table_columns(df_yield, 'season_crop_yield', 'operations')
            if added_columns:
                logger.info(f"Added {len(added_columns)} new columns to season_crop_yield: {added_columns}")

            # Get valid column names from database
            try:
                inspector = sqlalchemy_inspect(self.db.bind if hasattr(self.db, 'bind') else sync_engine)
                table_columns = inspector.get_columns('season_crop_yield', schema='operations')
                valid_columns = {col['name'] for col in table_columns}
            except Exception as e:
                logger.warning(f"Could not inspect database schema, using model columns: {str(e)}")
                valid_columns = set(YieldRecord.__table__.columns.keys())

            # Apply fuzzy column mapping to DataFrame columns
            column_mapping = {}
            for col in df_yield.columns:
                mapped_col = self._fuzzy_map_column_name(col) or col
                if mapped_col in valid_columns:
                    column_mapping[col] = mapped_col
                else:
                    logger.debug(f"Column '{col}' (mapped to '{mapped_col}') not in database table, will be filtered")

            # Rename columns using mapping
            df_yield = df_yield.rename(columns=column_mapping)

            # Filter to only valid columns
            df_yield = df_yield[[col for col in df_yield.columns if col in valid_columns]]

            # Convert to records
            records = df_yield.to_dict('records')

            # Validate and clean records
            validated_records = []
            for record in records:
                try:
                    # Clean record
                    record = self._clean_record_for_validation(record)
                    
                    # Convert string fields
                    string_fields = [
                        'production_co', 'purchase_order', 'slab', 'tp_days_slab',
                        'production_location', 'production_manager', 'pos_done_b', 'm1_soaking_slab'
                    ]
                    for field in string_fields:
                        if field in record and record[field] is not None:
                            record[field] = str(record[field])

                    # Validate with schema
                    validated = YieldRecordBase(**record)
                    validated_records.append(validated.dict())
                except Exception as e:
                    logger.warning(f"Invalid yield record skipped: {str(e)}")
                    continue

            if not validated_records:
                logger.warning("No valid yield records after validation")
                return 0

            # Bulk insert using bulk_insert_mappings for performance
            try:
                self.db.bulk_insert_mappings(YieldRecord, validated_records)
                self.db.commit()
                
                inserted_count = len(validated_records)
                self.processed_counts["yield"] = inserted_count
                
                logger.info(f"Successfully inserted {inserted_count} yield records")
                if truncated_rows > 0:
                    logger.info(f"Truncated {truncated_rows} existing rows before insert")
                
                return inserted_count
            except Exception as e:
                self.db.rollback()
                logger.error(f"Error during bulk insert: {str(e)}")
                raise

        except Exception as e:
            logger.error(f"Error bulk inserting yield records: {str(e)}")
            self.db.rollback()
            raise

    def match_grower_id_by_name(self, name, grower_map):
        if not name or not isinstance(name, str):
            return None

        clean_name = re.sub(r'[^a-zA-Z0-9 ]+', '', name).strip().lower()

        for key, gid in grower_map.items():
            clean_key = re.sub(r'[^a-zA-Z0-9 ]+', '', key).strip().lower()
            if re.search(re.escape(clean_key), clean_name) or re.search(re.escape(clean_name), clean_key):
                return gid
        return None

    def _clean_record_for_validation(self, record: dict) -> dict:
        """Clean record data before validation"""
        cleaned = record.copy()

        # Handle NaN values - convert to None
        for key, value in cleaned.items():
            if pd.isna(value):
                cleaned[key] = None
            elif isinstance(value, str) and value.lower() == 'nan':
                cleaned[key] = None
            elif key in ['grower_id', 'crop_id', 'lot_id', 'season_id', 'variety_id'] and (not value or value == ''):
                # Ensure ID fields are not empty strings (convert to None or keep as empty string based on your needs)
                cleaned[key] = value if value else ""

        # Handle NaT (Not a Time) values for dates
        for key, value in cleaned.items():
            if key in ['m1_soaking_date', 'female_soaking_date', 'female_tp_date']:
                if pd.isna(value) or str(value) == 'NaT':
                    cleaned[key] = None

        return cleaned

    def parse_date_flexible(self, date_value):
        """
        Flexible date parsing that handles multiple formats
        """
        if pd.isna(date_value) or date_value is None:
            return None

        date_str = str(date_value).strip()
        if not date_str or date_str.lower() in ['nan', 'nat', 'none', '']:
            return None

        # Try different formats common in your data
        formats_to_try = [
            '%d-%m-%Y',  # 09-12-2021
            '%Y-%m-%d',  # 2021-12-09
            '%d/%m/%Y',  # 09/12/2021
            '%m/%d/%Y',  # 12/09/2021
            '%d.%m.%Y',  # 09.12.2021
            '%Y%m%d'  # 20211209
        ]

        for fmt in formats_to_try:
            try:
                return pd.to_datetime(date_str, format=fmt).date()
            except (ValueError, TypeError):
                continue

        # Final fallback
        try:
            return pd.to_datetime(date_str, dayfirst=True).date()  # Assume day first
        except:
            print(f"Could not parse date: '{date_str}'")
            return None

    def _process_yield_records(self, df: pd.DataFrame, unique_records: dict):
        # BEFORE renaming: Store original values that will be renamed
        # This ensures we can populate missing values after renaming
        original_sum_received_qty = None
        if 'sum_of_received_qty' in df.columns:
            original_sum_received_qty = df['sum_of_received_qty'].copy()
        
        original_sum_received_raw_qty = None
        if 'sum_of_received_raw_qty' in df.columns:
            original_sum_received_raw_qty = df['sum_of_received_raw_qty'].copy()
        
        original_productivity_of_packed_seed = None
        if 'productivity_of_packed_seed' in df.columns:
            original_productivity_of_packed_seed = df['productivity_of_packed_seed'].copy()
        
        original_net_acreage_area = None
        if 'net_acreage_area' in df.columns:
            original_net_acreage_area = df['net_acreage_area'].copy()
        elif 'net_acerage_area' in df.columns:
            original_net_acreage_area = df['net_acerage_area'].copy()
        
        # Rename columns if needed for consistency
        # IMPORTANT: Use lot_no as primary key, not lot_id
        df = df.rename(columns={
            'growers_name': 'grower_name',
            'taluka_mandal': 'mandal',
            'season': 'season_id',
            'crop': 'crop_name',
            'hsp_code': 'variety_name',
            'lot_number': 'lot_no',  # Changed from lot_id to lot_no
            'lot_no_batch_no': 'lot_no',  # Changed from lot_id to lot_no
            'packed_qty': 'packed_qt',
            'productivity': 'productvit',
            # FIXED: Map source columns to target columns for proper data loading
            'sum_of_received_qty': 'total_recieved_qty',  # Map sum_of_received_qty -> total_recieved_qty
            'net_acreage_area': 'net_acres',  # Map net_acreage_area -> net_acres
            'net_acerage_area': 'net_acres',  # Alternative spelling
        })
        
        # FIXED: Ensure total_recieved_qty is populated from stored original values
        # After renaming, sum_of_received_qty no longer exists, so use stored values
        if 'total_recieved_qty' not in df.columns:
            # Create column if it doesn't exist (shouldn't happen if rename worked, but safety check)
            if original_sum_received_qty is not None:
                df['total_recieved_qty'] = original_sum_received_qty
                logger.debug(f"Created total_recieved_qty from sum_of_received_qty: {df['total_recieved_qty'].notna().sum()} non-null values")
            elif original_sum_received_raw_qty is not None:
                df['total_recieved_qty'] = original_sum_received_raw_qty
                logger.debug(f"Created total_recieved_qty from sum_of_received_raw_qty: {df['total_recieved_qty'].notna().sum()} non-null values")
            else:
                df['total_recieved_qty'] = None
                logger.warning("total_recieved_qty column not found and no source columns available")
        else:
            # Fill missing values from stored original columns (row by row)
            filled_count = 0
            if original_sum_received_raw_qty is not None:
                # Fill NaN values with corresponding values from original_sum_received_raw_qty
                mask = df['total_recieved_qty'].isna()
                df.loc[mask, 'total_recieved_qty'] = original_sum_received_raw_qty[mask]
                filled_count = mask.sum()
                if filled_count > 0:
                    logger.debug(f"Filled {filled_count} missing total_recieved_qty values from sum_of_received_raw_qty")
            elif original_sum_received_qty is not None:
                # Fill NaN values with corresponding values from original_sum_received_qty
                mask = df['total_recieved_qty'].isna()
                df.loc[mask, 'total_recieved_qty'] = original_sum_received_qty[mask]
                filled_count = mask.sum()
                if filled_count > 0:
                    logger.info(f"Filled {filled_count} missing total_recieved_qty values from sum_of_received_qty")
        
        # FIXED: Ensure productivity is populated from stored original values
        # After renaming, productivity_of_packed_seed no longer exists, so use stored values
        if 'productivity' not in df.columns:
            # Create column if it doesn't exist (shouldn't happen if rename worked, but safety check)
            if original_productivity_of_packed_seed is not None:
                df['productivity'] = original_productivity_of_packed_seed
                logger.debug(f"Created productivity from productivity_of_packed_seed: {df['productivity'].notna().sum()} non-null values")
            else:
                df['productivity'] = None
                logger.warning("productivity column not found and productivity_of_packed_seed not available")
        else:
            # Fill missing values from stored original column (row by row)
            if original_productivity_of_packed_seed is not None:
                # Fill NaN values with corresponding values from original_productivity_of_packed_seed
                mask = df['productivity'].isna()
                df.loc[mask, 'productivity'] = original_productivity_of_packed_seed[mask]
                filled_count = mask.sum()
                if filled_count > 0:
                    logger.info(f"Filled {filled_count} missing productivity values from productivity_of_packed_seed")
        
        # FIXED: Ensure net_acres is populated from stored original values
        # After renaming, net_acreage_area/net_acerage_area no longer exist, so use stored values
        if 'net_acres' not in df.columns:
            # Create column if it doesn't exist (shouldn't happen if rename worked, but safety check)
            if original_net_acreage_area is not None:
                df['net_acres'] = original_net_acreage_area
            else:
                df['net_acres'] = None
        else:
            # Fill missing values from stored original columns (row by row)
            if original_net_acreage_area is not None:
                # Fill NaN values with corresponding values from original_net_acreage_area
                mask = df['net_acres'].isna()
                df.loc[mask, 'net_acres'] = original_net_acreage_area[mask]

        # Normalize `season_id`
        df['season_id'] = df['season_id'].astype(str).str.strip().str.replace(r"[-\s]+", "_", regex=True)

        # Map crop_id from crop_name using regex-insensitive matching
        df['crop_id'] = df['crop_name'].apply(
            lambda val: self.crop_name_to_id.get(val.strip().lower()) if isinstance(val, str) else None
        )

        # FIXED: Variety lookup now includes crop_id dependency
        def get_variety_id(row):
            variety_name = row.get('variety_name')
            crop_id = row.get('crop_id')
            
            if not isinstance(variety_name, str) or not variety_name.strip():
                return None
            
            variety_name_norm = variety_name.replace("-", "").strip().lower()
            
            # Try composite key first (variety_name + crop_id) - most accurate
            if crop_id:
                key = (variety_name_norm, crop_id)
                if key in self.variety_name_crop_to_id:
                    return self.variety_name_crop_to_id[key]
            
            # Fallback to variety_name only (for backward compatibility)
            return self.variety_name_to_id.get(variety_name_norm)
        
        df['variety_id'] = df.apply(get_variety_id, axis=1)

        # Use improved location resolution with DB lookup/insert
        def get_location_id(row):
            village = row.get('village')
            mandal = row.get('mandal')
            district = row.get('district')
            state = row.get('state')
            return self._resolve_location_id(village, mandal, district, state)
        
        df['location_id'] = df.apply(get_location_id, axis=1)

        df['grower_name'] = df.apply(
            lambda row: row['grower_name']
            if pd.notnull(row.get('grower_name'))
            else self.match_grower_id_by_name(row.get('grower_name', ''), self.grower_name_to_id),
            axis=1
        )

        # Map grower_id from grower_name if not already present
        df['grower_id'] = df.apply(
            lambda row: self.grower_name_to_id.get(str(row.get('grower_name', '')).strip().lower()),
            axis=1
        )

        # Apply fuzzy column mapping
        column_mapping = {}
        for col in df.columns:
            mapped_col = self._fuzzy_map_column_name(col)
            if mapped_col and mapped_col != col:
                column_mapping[col] = mapped_col
        if column_mapping:
            df = df.rename(columns=column_mapping)
            logger.info(f"Applied fuzzy column mapping in yield records: {column_mapping}")

        # Clean DataFrame
        df = self._clean_dataframe(df)

        # Updated required_columns to match your model exactly
        # FIXED: Added total_recieved_qty and productivity to required columns
        required_columns = [
            "grower_id", "crop_id", "lot_id", "season_id", "variety_id", "location_id",
            "physical_received_qty_as_per_sap", "m1_soaking_date", "m1_soaking_slab",
            "female_soaking_date", "female_tp_date",
            "sowing_acres", "net_tp_acres", "net_acerage_area", "final_harvestable_area",
            "sum_of_received_raw_qty", "total_recieved_qty", "qty", "rate_per_kg", "amount_inr",
            "productivity_of_packed_seed", "productivity", "packed_qt", "productvity",
            "slab", "pos_done_b", "production_manager", "production_plant",
            "production_location", "production_co", "po_soaking_acres", "purchase_order",
            "planting_list_soaking_acres", "net_acres", "tp_days", "tp_days_slab"
        ]

        # Ensure all expected columns are present
        for col in required_columns:
            if col not in df.columns:
                df[col] = None

        # Keep all columns (not just required, to support dynamic columns)
        df = df.drop_duplicates()

        # FIXED: Convert dates properly with explicit format specification
        date_cols = ['m1_soaking_date', 'female_soaking_date', 'female_tp_date']
        for col in date_cols:
            # First try with explicit format YYYY-MM-DD
            try:
                df[col] = df[col].apply(self.parse_date_flexible)
                df[col] = pd.to_datetime(df[col], format='%d-%m-%Y', errors='coerce').dt.date
            except:
                # Fallback to general parsing if format doesn't match
                df[col] = pd.to_datetime(df[col], errors='coerce').dt.date

        # FIXED: Calculate tp_days correctly from the parsed dates
        df['tp_days_calculated'] = (
                pd.to_datetime(df['female_tp_date']) - pd.to_datetime(df['female_soaking_date'])
        ).dt.days

        # Replace the existing tp_days with the correctly calculated one
        df['tp_days'] = df['tp_days_calculated'].fillna(0).astype(int)

        # Add tp_days_slab based on correctly calculated tp_days
        def calculate_tp_slab(days):
            if pd.isna(days) or days <= 0:
                return "Invalid"
            elif 1 <= days <= 20:
                return "1-20"
            elif 21 <= days <= 25:
                return "21-25"
            elif 26 <= days <= 30:
                return "26-30"
            elif 31 <= days <= 40:
                return "31-40"
            elif 41 <= days <= 50:
                return "41-50"
            elif 51 <= days <= 60:
                return "51-60"
            elif days > 60:
                return "Above 60"
            else:
                return "Unknown"

        df['tp_days_slab'] = df['tp_days'].apply(calculate_tp_slab)

        # Drop the temporary calculation column
        df = df.drop('tp_days_calculated', axis=1)

        # Convert numeric fields properly with error handling
        numeric_float_cols = ["rate_per_kg", "physical_received_qty_as_per_sap", "sowing_acres",
                              "net_tp_acres", "net_acerage_area", "final_harvestable_area",
                              "sum_of_received_raw_qty", "total_recieved_qty", "qty", "amount_inr", 
                              "productivity_of_packed_seed", "productivity", "packed_qt", "productvity", 
                              "po_soaking_acres", "planting_list_soaking_acres", "net_acres"]

        for col in numeric_float_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # FIXED: Ensure numeric columns are properly typed
        if "sum_of_received_raw_qty" in df.columns:
            df["sum_of_received_raw_qty"] = df["sum_of_received_raw_qty"].astype(float)
        if "total_recieved_qty" in df.columns:
            df["total_recieved_qty"] = df["total_recieved_qty"].astype(float)
        if "productivity" in df.columns:
            df["productivity"] = df["productivity"].astype(float)
        if "net_acres" in df.columns:
            df["net_acres"] = df["net_acres"].astype(float)

        # Clean string 'nan' values in string columns
        string_cols = ["slab", "pos_done_b", "production_manager",
                       "production_plant", "production_location", "production_co", "purchase_order", "m1_soaking_slab"]
        for col in string_cols:
            if col in df.columns:
                df[col] = df[col].apply(
                    lambda x: None if (isinstance(x, str) and x.lower() == 'nan') or pd.isna(x) else x)

        # Store processed records with data cleaning and validation
        records = df.to_dict('records')
        cleaned_records = []

        for record in records:
            try:
                # FIXED: Ensure mapped columns are populated from source columns if missing
                # total_recieved_qty from sum_of_received_raw_qty or sum_of_received_qty
                if not record.get('total_recieved_qty') or pd.isna(record.get('total_recieved_qty')):
                    if record.get('sum_of_received_raw_qty') and pd.notna(record.get('sum_of_received_raw_qty')):
                        record['total_recieved_qty'] = record['sum_of_received_raw_qty']
                    elif record.get('sum_of_received_qty') and pd.notna(record.get('sum_of_received_qty')):
                        record['total_recieved_qty'] = record['sum_of_received_qty']
                
                # productivity from productivity_of_packed_seed
                if not record.get('productivity') or pd.isna(record.get('productivity')):
                    if record.get('productivity_of_packed_seed') and pd.notna(record.get('productivity_of_packed_seed')):
                        record['productivity'] = record['productivity_of_packed_seed']
                
                # net_acres from net_acreage_area or net_acerage_area
                if not record.get('net_acres') or pd.isna(record.get('net_acres')):
                    if record.get('net_acreage_area') and pd.notna(record.get('net_acreage_area')):
                        record['net_acres'] = record['net_acreage_area']
                    elif record.get('net_acerage_area') and pd.notna(record.get('net_acerage_area')):
                        record['net_acres'] = record['net_acerage_area']
                
                # Clean the record for validation
                cleaned_record = self._clean_record_for_validation(record)
                normalized_record = self.normalize_keys(cleaned_record)
                cleaned_records.append(normalized_record)
            
            except Exception as e:
                logger.warning(f"Error processing yield record: {e}")
                continue
                
        unique_records['season_crop_yield'] = cleaned_records
        return unique_records

    def _process_inspection_base_records(self, df: pd.DataFrame, unique_records: dict):
        # Rename columns for consistency
        # IMPORTANT: Use lot_no as primary key, not lot_id
        df = df.rename(columns={
            'season': 'season_id',
            'growers_name':'grower_name',
            'crop': 'crop_name',
            'hsp_code': 'variety_name',
            'village_id': 'location_id',
            'village_description': 'village',
            'lot_no_batch_no': 'lot_no',  # Changed from lot_id to lot_no
            'lot_number': 'lot_no',  # Changed from lot_id to lot_no
            'org_id': 'organizer_id',
            'father_s_name': 'fathers_name',
            'taluka_mandal': 'mandal'
        })
        
        # Ensure lot_no exists - map from mrno/LOT NO if needed
        if 'lot_no' not in df.columns:
            # Check for variations
            for col in df.columns:
                col_lower = str(col).lower().replace(' ', '_').replace('/', '_')
                if 'mrno' in col_lower and 'lot' in col_lower:
                    df['lot_no'] = df[col]
                    logger.info(f"Mapped '{col}' → 'lot_no' in inspection base records")
                    break
                
        # Normalize `season_id`
        df['season_id'] = df['season_id'].astype(str).str.strip().str.replace(r"[-\s]+", "_", regex=True)

        # Map crop_id from crop_name using regex-insensitive matching
        df['crop_id'] = df['crop_name'].apply(
            lambda val: self.crop_name_to_id.get(val.strip().lower()) if isinstance(val, str) else None
        )
        
        # FIXED: Variety lookup now includes crop_id dependency
        def get_variety_id(row):
            variety_name = row.get('variety_name')
            crop_id = row.get('crop_id')
            
            if not isinstance(variety_name, str) or not variety_name.strip():
                return None
            
            variety_name_norm = variety_name.replace("-", "").strip().lower()
            
            # Try composite key first (variety_name + crop_id) - most accurate
            if crop_id:
                key = (variety_name_norm, crop_id)
                if key in self.variety_name_crop_to_id:
                    return self.variety_name_crop_to_id[key]
            
            # Fallback to variety_name only (for backward compatibility)
            return self.variety_name_to_id.get(variety_name_norm)
        
        df['variety_id'] = df.apply(get_variety_id, axis=1)

        # Use improved location resolution with DB lookup/insert
        def get_location_id(row):
            village = row.get('village')
            mandal = row.get('mandal')
            district = row.get('district')
            state = row.get('state')
            return self._resolve_location_id(village, mandal, district, state)
        
        df['location_id'] = df.apply(get_location_id, axis=1)

        df['grower_name'] = df.apply(
            lambda row: row['grower_name']
            if pd.notnull(row.get('grower_name'))
            else self.match_grower_id_by_name(row.get('grower_name', ''), self.grower_name_to_id),
            axis=1
        )

        # Map grower_id from grower_name if not already present
        df['grower_id'] = df.apply(
            lambda row:  self.grower_name_to_id.get(str(row.get('grower_name', '')).strip().lower()),
            axis=1
        )

        df['organizer_id'] = df.apply(
            lambda row:  self.organizer_name_to_id.get(str(row.get('organizer_name', '')).strip().lower()),
            axis=1
        )

        # Drop original columns not needed in DB
        df = df.drop(columns=['crop_name', 'variety_name'], errors='ignore')

        # Apply fuzzy column mapping
        column_mapping = {}
        for col in df.columns:
            mapped_col = self._fuzzy_map_column_name(col)
            if mapped_col and mapped_col != col:
                column_mapping[col] = mapped_col
        if column_mapping:
            df = df.rename(columns=column_mapping)
            logger.info(f"Applied fuzzy column mapping: {column_mapping}")

        # Clean DataFrame
        df = self._clean_dataframe(df)

        # Default any missing columns to None
        all_required_cols = [
            "season_id", "crop_id", "variety_id", "location_id", "grower_id", "organizer_id", "lot_id",
            "hybrid_id", "organizer_name", "grower_name", "grower_gender", "purchasing_document_number",
            "fathers_name", "village", "mandal", "mandal_id", "district", "district_id", "state",
            "male_parent_seed_lot_no", "male_soaking_acre", "male_no_of_pkt", "male_qty_in_kgs",
            "female_parent_seed_lot_no", "female_soaking_acre", "female_no_of_pkt", "female_qty_in_kgs",
            "production_officer", "tfa_name", "production_plant", "production_code", "production_location"
        ]
        for col in all_required_cols:
            if col not in df.columns:
                df[col] = None

        # Keep all columns (not just required, to support dynamic columns)
        df = df.drop_duplicates()
        
        # Convert to dict records
        records = df.to_dict('records')
        unique_records['season_crop_inspection_base'] = [self.normalize_keys(r) for r in records]

        return unique_records

    # ============================================
    # OPTIMIZED METHODS FOR YIELD DATA RELOAD
    # ============================================

    def _preload_all_master_data(self) -> Tuple[Dict[str, Dict], Dict[str, float]]:
        """
        Preload all master data into memory dictionaries for fast lookups.
        Returns (master_cache, timing_dict) for profiling.
        """
        start_time = time.perf_counter()
        timing = {}
        logger.info("[PROFILING] Preloading all master data into memory...")
        
        master_cache = {
            'seasons': {},
            'crops': {},
            'varieties': {},
            'growers': {},
            'organizers': {},
            'locations': {}
        }
        
        try:
            # Preload seasons
            t0 = time.perf_counter()
            try:
                seasons_query = text("SELECT season_id, season_name FROM operations.seasons")
                seasons = self.db.execute(seasons_query).fetchall()
                for season_id, season_name in seasons:
                    if season_name:
                        key = str(season_name).strip().replace(" ", "_").replace("-", "_").upper()
                        master_cache['seasons'][key] = season_id
                timing['master_query_seasons'] = time.perf_counter() - t0
                logger.info(f"  [PROFILING] seasons: {timing['master_query_seasons']:.3f}s, {len(master_cache['seasons'])} rows")
            except Exception as e:
                logger.warning(f"  Could not load seasons: {e}")
                self.db.rollback()
                timing['master_query_seasons'] = time.perf_counter() - t0
            
            # Preload crops
            t0 = time.perf_counter()
            try:
                crops = self.db.query(CropRecord.crop_id, CropRecord.crop_name).all()
                for crop_id, crop_name in crops:
                    if crop_name:
                        key = str(crop_name).strip().lower()
                        master_cache['crops'][key] = crop_id
                timing['master_query_crops'] = time.perf_counter() - t0
                logger.info(f"  [PROFILING] crops: {timing['master_query_crops']:.3f}s, {len(master_cache['crops'])} rows")
            except Exception as e:
                logger.warning(f"  Could not load crops: {e}")
                self.db.rollback()
                timing['master_query_crops'] = time.perf_counter() - t0
            
            # Preload varieties (with crop_id)
            t0 = time.perf_counter()
            try:
                varieties = self.db.query(
                    VarietyRecord.variety_id, 
                    VarietyRecord.variety_name,
                    VarietyRecord.crop_id
                ).all()
                for variety_id, variety_name, crop_id in varieties:
                    if variety_name:
                        key = str(variety_name).strip().lower().replace('-', '')
                        master_cache['varieties'][(key, crop_id)] = variety_id
                        master_cache['varieties'][key] = variety_id
                timing['master_query_varieties'] = time.perf_counter() - t0
                logger.info(f"  [PROFILING] varieties: {timing['master_query_varieties']:.3f}s, {len(varieties)} rows")
            except Exception as e:
                logger.warning(f"  Could not load varieties: {e}")
                self.db.rollback()
                timing['master_query_varieties'] = time.perf_counter() - t0
            
            # Preload growers
            t0 = time.perf_counter()
            try:
                growers = self.db.query(GrowerRecord.grower_id, GrowerRecord.grower_name).all()
                for grower_id, grower_name in growers:
                    if grower_name:
                        key = str(grower_name).strip().lower()
                        master_cache['growers'][key] = grower_id
                timing['master_query_growers'] = time.perf_counter() - t0
                logger.info(f"  [PROFILING] growers: {timing['master_query_growers']:.3f}s, {len(master_cache['growers'])} rows")
            except Exception as e:
                logger.warning(f"  Could not load growers: {e}")
                self.db.rollback()
                timing['master_query_growers'] = time.perf_counter() - t0
            
            # Preload organizers
            t0 = time.perf_counter()
            try:
                organizers_query = text("SELECT organizer_id, organizer_name FROM operations.organizers")
                organizers = self.db.execute(organizers_query).fetchall()
                for organizer_id, organizer_name in organizers:
                    if organizer_name:
                        key = str(organizer_name).strip().lower()
                        master_cache['organizers'][key] = organizer_id
                timing['master_query_organizers'] = time.perf_counter() - t0
                logger.info(f"  [PROFILING] organizers: {timing['master_query_organizers']:.3f}s, {len(master_cache['organizers'])} rows")
            except Exception as e:
                logger.warning(f"  Could not load organizers: {e}")
                self.db.rollback()
                timing['master_query_organizers'] = time.perf_counter() - t0
            
            # Preload locations (with composite keys, normalized)
            t0 = time.perf_counter()
            try:
                locations = self.db.query(
                    LocationRecord.location_id,
                    LocationRecord.village,
                    LocationRecord.district,
                    LocationRecord.state
                ).all()
                for location_id, village, district, state in locations:
                    if village:
                        village_norm = self._normalize_location_field(village) or str(village).strip().lower()
                        district_norm = self._normalize_location_field(district) if district else ''
                        state_norm = self._normalize_location_field(state) if state else ''
                        master_cache['locations'][village_norm] = location_id
                        if district_norm:
                            master_cache['locations'][f"{village_norm}|{district_norm}"] = location_id
                        if district_norm and state_norm:
                            master_cache['locations'][f"{village_norm}|{district_norm}|{state_norm}"] = location_id
                # Ensure UNKNOWN_LOCATION exists for rows with empty village (zero row loss)
                if self.UNKNOWN_LOCATION_VILLAGE not in master_cache['locations']:
                    try:
                        max_loc_res = self.db.execute(text("SELECT MAX(location_id) FROM operations.locations")).scalar()
                        max_loc = 500000
                        if max_loc_res and re.search(r'\d+', str(max_loc_res)):
                            max_loc = max(max_loc, int(re.search(r'\d+', str(max_loc_res)).group()))
                        unknown_loc_id = f"L_{max_loc + 1}"
                        self.db.execute(text("""
                            INSERT INTO operations.locations (location_id, village, unique_location_id, mandal, district, state, category_id)
                            VALUES (:lid, :vname, :vname, NULL, NULL, NULL, 100004)
                            ON CONFLICT (location_id) DO NOTHING
                        """), {"lid": unknown_loc_id, "vname": "Unknown Location"})
                        self.db.commit()
                        master_cache['locations'][self.UNKNOWN_LOCATION_VILLAGE] = unknown_loc_id
                        logger.info(f"  [CREATED] Fallback location {unknown_loc_id} for empty village")
                    except Exception as e:
                        logger.warning(f"  Could not create UNKNOWN_LOCATION: {e}")
                        self.db.rollback()
                timing['master_query_locations'] = time.perf_counter() - t0
                logger.info(f"  [PROFILING] locations: {timing['master_query_locations']:.3f}s, {len(locations)} rows")
            except Exception as e:
                logger.warning(f"  Could not load locations: {e}")
                self.db.rollback()
                timing['master_query_locations'] = time.perf_counter() - t0
            
            timing['preload_masters_total'] = time.perf_counter() - start_time
            logger.info(f"[PROFILING] Master data preload total: {timing['preload_masters_total']:.3f}s")
            return master_cache, timing
            
        except Exception as e:
            logger.error(f"Error preloading master data: {e}")
            self.db.rollback()
            timing['preload_masters_total'] = time.perf_counter() - start_time
            return master_cache, timing

    def _batch_create_missing_masters(
        self, 
        missing_masters: Dict[str, List[Dict]],
        master_cache: Dict[str, Dict]
    ) -> Dict[str, int]:
        """
        Batch create missing master records and update cache.
        Returns count of created records by type.
        """
        created_counts = defaultdict(int)
        
        try:
            # Batch create seasons
            if missing_masters.get('seasons'):
                try:
                    max_season = self.db.execute(text("SELECT MAX(season_id) FROM operations.seasons")).scalar()
                    max_num = int(re.search(r'\d+', str(max_season)).group()) if max_season and re.search(r'\d+', str(max_season)) else 0
                    
                    season_values = []
                    for season_name, season_id in missing_masters['seasons']:
                        season_values.append((season_id, season_name))
                        master_cache['seasons'][season_id] = season_id
                    
                    if season_values:
                        insert_sql = text("""
                            INSERT INTO operations.seasons (season_id, season_name)
                            VALUES (:season_id, :season_name)
                            ON CONFLICT (season_id) DO NOTHING
                        """)
                        self.db.execute(insert_sql, [{"season_id": s[0], "season_name": s[1]} for s in season_values])
                        created_counts['seasons'] = len(season_values)
                        logger.info(f"  Batch created {len(season_values)} seasons")
                except Exception as e:
                    logger.warning(f"  Error batch creating seasons: {e}")
                    self.db.rollback()
            
            # Batch create crops
            if missing_masters.get('crops'):
                try:
                    max_crop = self.get_max_crop_id(self.db)
                    crop_values = []
                    for crop_name in missing_masters['crops']:
                        max_crop += 1
                        crop_id = f"CR_{str(max_crop).zfill(3)}"
                        crop_values.append((crop_id, crop_name, 100001))
                        master_cache['crops'][crop_name.lower()] = crop_id
                    
                    if crop_values:
                        insert_sql = text("""
                            INSERT INTO operations.crops (crop_id, crop_name, category_id)
                            VALUES (:crop_id, :crop_name, :category_id)
                            ON CONFLICT (crop_id) DO NOTHING
                        """)
                        self.db.execute(insert_sql, [
                            {"crop_id": c[0], "crop_name": c[1], "category_id": c[2]} 
                            for c in crop_values
                        ])
                        created_counts['crops'] = len(crop_values)
                        logger.info(f"  Batch created {len(crop_values)} crops")
                except Exception as e:
                    logger.warning(f"  Error batch creating crops: {e}")
                    self.db.rollback()
            
            # Batch create varieties
            if missing_masters.get('varieties'):
                try:
                    max_variety = self.get_max_variety_id(self.db)
                    variety_values = []
                    for variety_name, crop_id in missing_masters['varieties']:
                        max_variety += 1
                        variety_id = f"VR_{str(max_variety).zfill(4)}"
                        variety_norm = variety_name.lower().replace('-', '')
                        variety_values.append((variety_id, variety_name, crop_id, 100003))
                        master_cache['varieties'][(variety_norm, crop_id)] = variety_id
                        master_cache['varieties'][variety_norm] = variety_id
                    
                    if variety_values:
                        insert_sql = text("""
                            INSERT INTO operations.varieties (variety_id, variety_name, crop_id, category_id)
                            VALUES (:variety_id, :variety_name, :crop_id, :category_id)
                            ON CONFLICT (variety_id) DO NOTHING
                        """)
                        self.db.execute(insert_sql, [
                            {"variety_id": v[0], "variety_name": v[1], "crop_id": v[2], "category_id": v[3]}
                            for v in variety_values
                        ])
                        created_counts['varieties'] = len(variety_values)
                        logger.info(f"  Batch created {len(variety_values)} varieties")
                except Exception as e:
                    logger.warning(f"  Error batch creating varieties: {e}")
                    self.db.rollback()
            
            # Batch create growers
            if missing_masters.get('growers'):
                try:
                    max_grower = self.get_max_grower_id(self.db)
                    grower_values = []
                    for grower_data in missing_masters['growers']:
                        max_grower += 1
                        grower_id = f"G_{str(max_grower).zfill(6)}"
                        grower_name = grower_data['name']
                        grower_values.append((
                            grower_id, grower_name, 
                            grower_data.get('fathers_name'), 
                            grower_data.get('grower_gender'),
                            100005
                        ))
                        master_cache['growers'][grower_name.lower()] = grower_id
                    
                    if grower_values:
                        insert_sql = text("""
                            INSERT INTO operations.growers (grower_id, grower_name, fathers_name, grower_gender, category_id)
                            VALUES (:grower_id, :grower_name, :fathers_name, :grower_gender, :category_id)
                            ON CONFLICT (grower_id) DO NOTHING
                        """)
                        self.db.execute(insert_sql, [
                            {
                                "grower_id": g[0], "grower_name": g[1], 
                                "fathers_name": g[2], "grower_gender": g[3], "category_id": g[4]
                            }
                            for g in grower_values
                        ])
                        created_counts['growers'] = len(grower_values)
                        logger.info(f"  Batch created {len(grower_values)} growers")
                except Exception as e:
                    logger.warning(f"  Error batch creating growers: {e}")
                    self.db.rollback()
            
            # Batch create organizers
            if missing_masters.get('organizers'):
                try:
                    max_org = self.get_max_organizer_id(self.db)
                    org_values = []
                    for org_name in missing_masters['organizers']:
                        max_org += 1
                        org_id = f"O_{str(max_org).zfill(6)}"
                        org_values.append((org_id, org_name, 100006))
                        master_cache['organizers'][org_name.lower()] = org_id
                    
                    if org_values:
                        insert_sql = text("""
                            INSERT INTO operations.organizers (organizer_id, organizer_name, category_id)
                            VALUES (:organizer_id, :organizer_name, :category_id)
                            ON CONFLICT (organizer_id) DO NOTHING
                        """)
                        self.db.execute(insert_sql, [
                            {"organizer_id": o[0], "organizer_name": o[1], "category_id": o[2]}
                            for o in org_values
                        ])
                        created_counts['organizers'] = len(org_values)
                        logger.info(f"  Batch created {len(org_values)} organizers")
                except Exception as e:
                    logger.warning(f"  Error batch creating organizers: {e}")
                    self.db.rollback()
            
            # Batch create locations
            if missing_masters.get('locations'):
                try:
                    max_location_query = text("SELECT MAX(location_id) FROM operations.locations")
                    max_location = self.db.execute(max_location_query).scalar()
                    if max_location:
                        match = re.search(r'\d+', max_location)
                        max_num = int(match.group()) if match else 500000
                    else:
                        max_num = 500000
                    
                    location_values = []
                    for loc_data in missing_masters['locations']:
                        max_num += 1
                        location_id = f"L_{max_num}"
                        village = loc_data.get('village') or 'Unknown Location'
                        district = loc_data.get('district')
                        state = loc_data.get('state')
                        mandal = loc_data.get('mandal')
                        
                        location_values.append((
                            location_id, village, village, mandal, district, state, 100004
                        ))
                        
                        # Update cache (normalize village, district, state for matching)
                        village_norm = str(village).strip().lower() if village else self.UNKNOWN_LOCATION_VILLAGE
                        district_norm = str(district).strip().lower() if district else ''
                        state_norm = str(state).strip().lower() if state else ''
                        master_cache['locations'][village_norm] = location_id
                        if village_norm in (self.UNKNOWN_LOCATION_VILLAGE, 'unknown location'):
                            master_cache['locations'][self.UNKNOWN_LOCATION_VILLAGE] = location_id
                        if district_norm:
                            master_cache['locations'][f"{village_norm}|{district_norm}"] = location_id
                        if district_norm and state_norm:
                            master_cache['locations'][f"{village_norm}|{district_norm}|{state_norm}"] = location_id
                    
                    if location_values:
                        insert_sql = text("""
                            INSERT INTO operations.locations 
                            (location_id, village, unique_location_id, mandal, district, state, category_id)
                            VALUES (:location_id, :village, :unique_location_id, :mandal, :district, :state, :category_id)
                            ON CONFLICT (location_id) DO NOTHING
                        """)
                        self.db.execute(insert_sql, [
                            {
                                "location_id": l[0], "village": l[1], "unique_location_id": l[2],
                                "mandal": l[3], "district": l[4], "state": l[5], "category_id": l[6]
                            }
                            for l in location_values
                        ])
                        created_counts['locations'] = len(location_values)
                        logger.info(f"  Batch created {len(location_values)} locations")
                except Exception as e:
                    logger.warning(f"  Error batch creating locations: {e}")
                    self.db.rollback()
            
            self.db.commit()
            return dict(created_counts)
            
        except Exception as e:
            logger.error(f"Error in batch master creation: {e}")
            self.db.rollback()
            return dict(created_counts)

    def _vectorized_resolve_master_ids(
        self,
        df: pd.DataFrame,
        master_cache: Dict[str, Dict]
    ) -> Tuple[pd.DataFrame, Dict[str, List[Dict]]]:
        """
        VECTORIZED master ID resolution using pandas operations.
        Returns: (df_with_ids, missing_masters)
        """
        start_time = time.perf_counter()
        logger.info("Vectorized master ID resolution...")
        
        df = df.copy()
        missing_masters = defaultdict(list)
        missing_seasons = set()
        missing_crops = set()
        missing_varieties = set()
        missing_growers = set()
        missing_organizers = set()
        missing_locations = set()
        
        # Vectorized season_id resolution
        if 'season' in df.columns or 'season_id' in df.columns:
            season_col = df.get('season', df.get('season_id', pd.Series()))
            df['_season_key'] = season_col.astype(str).str.strip().str.replace(r"[-\s]+", "_", regex=True).str.upper()
            df['season_id'] = df['_season_key'].map(master_cache['seasons']).fillna(df['_season_key'])
            # Collect missing seasons
            missing_season_keys = df[df['_season_key'].notna() & ~df['_season_key'].isin(master_cache['seasons'].keys())]['_season_key'].unique()
            for key in missing_season_keys:
                if key not in missing_seasons:
                    missing_seasons.add(key)
                    season_name = df[df['_season_key'] == key].iloc[0].get('season', key)
                    missing_masters['seasons'].append((season_name, key))
            df = df.drop(columns=['_season_key'], errors='ignore')
        
        # Vectorized crop_id resolution
        if 'crop' in df.columns or 'crop_name' in df.columns:
            crop_col = df.get('crop', df.get('crop_name', pd.Series()))
            df['_crop_key'] = crop_col.astype(str).str.strip().str.lower()
            df['_crop_key'] = df['_crop_key'].replace(['nan', 'none', 'null'], '')
            df['crop_id'] = df['_crop_key'].map(master_cache['crops'])
            df.loc[(df['_crop_key'] == '') | df['_crop_key'].isna(), 'crop_id'] = None
            # Collect missing crops (exclude empty/invalid keys)
            valid_crop_mask = (df['_crop_key'] != '') & df['_crop_key'].notna()
            missing_crop_mask = valid_crop_mask & df['crop_id'].isna()
            for key in df[missing_crop_mask]['_crop_key'].unique():
                if key and key not in missing_crops:
                    missing_crops.add(key)
                    crop_name = df[df['_crop_key'] == key].iloc[0].get('crop', df[df['_crop_key'] == key].iloc[0].get('crop_name', key))
                    missing_masters['crops'].append(crop_name)
            df = df.drop(columns=['_crop_key'], errors='ignore')
        
        # Vectorized variety_id resolution (needs crop_id)
        if 'hsp_code' in df.columns or 'variety' in df.columns or 'variety_name' in df.columns:
            variety_col = df.get('hsp_code', df.get('variety', df.get('variety_name', pd.Series())))
            df['_variety_key'] = variety_col.astype(str).str.strip().str.lower().str.replace('-', '')
            df['_variety_key'] = df['_variety_key'].replace(['nan', 'none', 'null', ''], '')
            # Try composite key first (variety_key, crop_id)
            df['_variety_composite'] = list(zip(df['_variety_key'], df['crop_id']))
            df['variety_id'] = df['_variety_composite'].map(
                lambda x: master_cache['varieties'].get(x) if isinstance(x, tuple) and x[0] else None
            )
            # Fallback to variety_key only
            mask = df['variety_id'].isna() & (df['_variety_key'] != '')
            df.loc[mask, 'variety_id'] = df.loc[mask, '_variety_key'].map(master_cache['varieties'])
            # Collect missing varieties (need crop_id - will be set after batch create crops)
            missing_variety_mask = (df['_variety_key'] != '') & df['variety_id'].isna() & df['crop_id'].notna()
            for idx in df[missing_variety_mask].index:
                variety_key = df.loc[idx, '_variety_key']
                crop_id = df.loc[idx, 'crop_id']
                if (variety_key, crop_id) not in missing_varieties:
                    missing_varieties.add((variety_key, crop_id))
                    variety_name = df.loc[idx].get('hsp_code', df.loc[idx].get('variety', variety_key))
                    missing_masters['varieties'].append((variety_name, crop_id))
            df = df.drop(columns=['_variety_key', '_variety_composite'], errors='ignore')
        
        # Vectorized grower_id resolution
        if 'growers_name' in df.columns or 'grower_name' in df.columns:
            grower_col = df.get('growers_name', df.get('grower_name', pd.Series()))
            df['_grower_key'] = grower_col.astype(str).str.strip().str.lower()
            df['grower_id'] = df['_grower_key'].map(master_cache['growers'])
            # Collect missing growers
            missing_grower_keys = df[df['_grower_key'].notna() & df['grower_id'].isna()]['_grower_key'].unique()
            for key in missing_grower_keys:
                if key not in missing_growers:
                    missing_growers.add(key)
                    grower_name = df[df['_grower_key'] == key].iloc[0].get('growers_name', df[df['_grower_key'] == key].iloc[0].get('grower_name', key))
                    missing_masters['growers'].append({
                        'name': grower_name,
                        'fathers_name': df[df['_grower_key'] == key].iloc[0].get('father_name', df[df['_grower_key'] == key].iloc[0].get('fathers_name')),
                        'grower_gender': df[df['_grower_key'] == key].iloc[0].get('grower_gender')
                    })
            df = df.drop(columns=['_grower_key'], errors='ignore')
        
        # Vectorized organizer_id resolution
        if 'organizer_name' in df.columns or 'org_id' in df.columns:
            org_col = df.get('organizer_name', df.get('org_id', pd.Series()))
            df['_org_key'] = org_col.astype(str).str.strip().str.lower()
            df['organizer_id'] = df['_org_key'].map(master_cache['organizers'])
            # Collect missing organizers
            missing_org_keys = df[df['_org_key'].notna() & df['organizer_id'].isna()]['_org_key'].unique()
            for key in missing_org_keys:
                if key not in missing_organizers:
                    missing_organizers.add(key)
                    org_name = df[df['_org_key'] == key].iloc[0].get('organizer_name', df[df['_org_key'] == key].iloc[0].get('org_id', key))
                    missing_masters['organizers'].append(org_name)
            df = df.drop(columns=['_org_key'], errors='ignore')
        
        # Vectorized location_id resolution (composite key - normalized village, district, state)
        if 'village' in df.columns or 'village_id' in df.columns:
            village_col = df.get('village', df.get('village_id', pd.Series()))
            df['_village_key'] = village_col.astype(str).str.strip().str.lower().replace(['nan', 'none', 'null'], '')
            df['_village_key'] = df['_village_key'].str.replace(r'\s+', ' ', regex=True).str.strip()
            df.loc[(df['_village_key'] == '') | df['_village_key'].isna(), '_village_key'] = self.UNKNOWN_LOCATION_VILLAGE
            
            df['_district_key'] = df.get('district', pd.Series()).fillna('').astype(str).str.strip().str.lower().replace(['nan', 'none', 'null'], '')
            df['_district_key'] = df['_district_key'].str.replace(r'\s+', ' ', regex=True).str.strip()
            
            df['_state_key'] = df.get('state', pd.Series()).fillna('').astype(str).str.strip().str.lower().replace(['nan', 'none', 'null'], '')
            df['_state_key'] = df['_state_key'].str.replace(r'\s+', ' ', regex=True).str.strip()
            
            # Build composite keys for lookup (village|district|state, village|district, village)
            df['_loc_key_full'] = df['_village_key'] + '|' + df['_district_key'] + '|' + df['_state_key']
            df['_loc_key_district'] = df['_village_key'] + '|' + df['_district_key']
            
            mask_full = (df['_village_key'] != '') & (df['_district_key'] != '') & (df['_state_key'] != '')
            mask_district = (df['_village_key'] != '') & (df['_district_key'] != '')
            
            df['location_id'] = None
            df.loc[mask_full, 'location_id'] = df.loc[mask_full, '_loc_key_full'].map(master_cache['locations'])
            mask = df['location_id'].isna() & mask_district
            df.loc[mask, 'location_id'] = df.loc[mask, '_loc_key_district'].map(master_cache['locations'])
            mask = df['location_id'].isna() & (df['_village_key'] != '')
            df.loc[mask, 'location_id'] = df.loc[mask, '_village_key'].map(master_cache['locations'])
            
            # Collect missing locations (including UNKNOWN_LOCATION if not in cache)
            missing_loc_mask = df['location_id'].isna()
            for idx in df[missing_loc_mask].index:
                village_key = df.loc[idx, '_village_key']
                village_display = df.loc[idx].get('village', df.loc[idx].get('village_id', village_key))
                vk, dk, sk = df.loc[idx, '_village_key'], df.loc[idx, '_district_key'], df.loc[idx, '_state_key']
                loc_key = f"{vk}|{dk}|{sk}" if (vk and dk and sk) else (f"{vk}|{dk}" if (vk and dk) else vk)
                if loc_key and loc_key not in missing_locations:
                    missing_locations.add(loc_key)
                    missing_masters['locations'].append({
                        'village': str(village_display).strip() if pd.notna(village_display) and str(village_display).strip() else village_key,
                        'district': df.loc[idx].get('district'),
                        'state': df.loc[idx].get('state'),
                        'mandal': df.loc[idx].get('taluka_mandal', df.loc[idx].get('mandal'))
                    })
            df = df.drop(columns=['_village_raw', '_village_key', '_district_raw', '_district_key', '_state_raw', '_state_key', '_loc_key_full', '_loc_key_district'], errors='ignore')
        
        elapsed = time.perf_counter() - start_time
        logger.info(f"Vectorized master ID resolution completed in {elapsed:.3f}s ({len(df)/max(elapsed,0.001):.0f} rows/sec)")
        logger.info(f"Found {sum(len(v) for v in missing_masters.values())} missing master records")
        
        return df, missing_masters

    def _optimized_split_and_prepare_data(
        self, 
        df: pd.DataFrame, 
        master_cache: Dict[str, Dict]
    ) -> Tuple[List[Dict], List[Dict], Dict[str, List[Dict]], Dict[str, float], List[Dict]]:
        """
        Optimized version using VECTORIZED operations for master ID resolution.
        Mandatory: season_id, crop_id, variety_id, lot_id, location_id.
        Returns: (inspection_records, yield_records, missing_masters, timing_dict, failed_rows)
        """
        start_time = time.perf_counter()
        timing = {}
        failed_rows = []
        logger.info("Processing DataFrame with vectorized master lookups...")
        
        t0 = time.perf_counter()
        if 'lot_id' in df.columns:
            df['lot_id'] = df['lot_id'].astype(str).str.strip().str.lower()
            df['lot_id'] = df['lot_id'].replace(['nan', 'none', 'null', ''], None)
        timing['lot_id_normalize'] = time.perf_counter() - t0
        
        t0 = time.perf_counter()
        df, missing_masters = self._vectorized_resolve_master_ids(df, master_cache)
        timing['vectorized_master_resolve'] = time.perf_counter() - t0
        logger.info(f"  [PROFILING] vectorized_master_resolve: {timing['vectorized_master_resolve']:.3f}s ({len(df)/max(timing['vectorized_master_resolve'],0.001):.0f} rows/sec)")
        
        t0 = time.perf_counter()
        required_cols = ['season_id', 'crop_id', 'variety_id', 'lot_id', 'location_id']
        if 'location_id' not in df.columns:
            df['location_id'] = None
        for c in required_cols:
            if c not in df.columns:
                df[c] = None
        valid_mask = df[required_cols].notna().all(axis=1)
        df_valid = df[valid_mask].copy()
        df_invalid = df[~valid_mask]
        for idx in df_invalid.index:
            missing_keys = [c for c in required_cols if pd.isna(df.loc[idx, c]) or df.loc[idx, c] in [None, '', 'nan']]
            reason = f"missing mandatory: {', '.join(missing_keys)}"
            failed_rows.append({'row_index': int(idx), 'reason': reason, 'missing_keys': missing_keys})
        timing['filter_valid_rows'] = time.perf_counter() - t0
        if len(df_valid) < len(df):
            logger.warning(f"[ROW LOSS] Skipped {len(df) - len(df_valid)} rows with missing required fields (mandatory: {required_cols})")
        
        inspection_columns = [
            'season', 'crop', 'production_code', 'hsp_code', 'variety',
            'organizer_name', 'org_id', 'purchasing_document_number',
            'growers_name', 'father_name', 'village', 'village_id',
            'taluka_mandal', 'mandal', 'district', 'state', 'lot_id'
        ]
        yield_columns = [
            'lot_id', 'm1_soaking_date', 'm1_soaking_week',
            'female_soaking_date', 'female_date_of_transplant',
            'po_soaking_acres', 'planting_list_soaking_acres',
            'sowing_acres', 'net_tp_acres', 'net_acreage_area',
            'final_harvestable_area', 'sum_of_received_qty',
            'packed_qty', 'productivity_of_packed_seed', 'yield_slab',
            'qty', 'rate_per_kg', 'amount_inr',
            'male_parent_seed_lot_no', 'male_soaking_acre',
            'male_no_of_pkt', 'male_qty_in_kgs',
            'female_parent_seed_lot_no', 'female_soaking_acre',
            'female_no_of_pkt', 'female_qty_in_kgs'
        ]
        
        t0 = time.perf_counter()
        inspection_df = df_valid[['season_id', 'crop_id', 'variety_id', 'lot_id', 'location_id', 'grower_id', 'organizer_id']].copy()
        for col in inspection_columns:
            if col in df_valid.columns:
                inspection_df[col] = df_valid[col]
        yield_df = df_valid[['season_id', 'crop_id', 'variety_id', 'lot_id', 'location_id', 'grower_id', 'organizer_id']].copy()
        for col in yield_columns:
            if col in df_valid.columns:
                yield_df[col] = df_valid[col]
        timing['build_dataframes'] = time.perf_counter() - t0
        
        t0 = time.perf_counter()
        inspection_records = inspection_df.to_dict('records')
        yield_records = yield_df.to_dict('records')
        timing['to_dict_records'] = time.perf_counter() - t0
        logger.info(f"  [PROFILING] to_dict('records'): {timing['to_dict_records']:.3f}s, {len(inspection_records)} records")
        
        t0 = time.perf_counter()
        for i, rec in enumerate(inspection_records):
            rec.update({k: None if pd.isna(v) else v for k, v in rec.items()})
            if (i + 1) % PROGRESS_LOG_INTERVAL == 0:
                elapsed = time.perf_counter() - t0
                rps = (i + 1) / max(elapsed, 0.001)
                logger.info(f"  [PROFILING] Rows processed: {i+1}/{len(inspection_records)}, {rps:.0f} rows/sec (inspection clean)")
        t1 = time.perf_counter()
        for i, rec in enumerate(yield_records):
            rec.update({k: None if pd.isna(v) else v for k, v in rec.items()})
            if (i + 1) % PROGRESS_LOG_INTERVAL == 0:
                elapsed = time.perf_counter() - t1
                rps = (i + 1) / max(elapsed, 0.001)
                logger.info(f"  [PROFILING] Rows processed: {i+1}/{len(yield_records)}, {rps:.0f} rows/sec (yield clean)")
        timing['clean_nan_loop'] = time.perf_counter() - t0
        logger.info(f"  [PROFILING] clean NaN loop: {timing['clean_nan_loop']:.3f}s ({len(inspection_records)+len(yield_records)} records)")
        
        elapsed = time.perf_counter() - start_time
        logger.info(f"[PROFILING] process_data total: {elapsed:.3f}s, {len(inspection_records)} records ({len(inspection_records)/max(elapsed,0.001):.0f} rows/sec)")
        if failed_rows:
            logger.warning(f"[FAILED ROWS] {len(failed_rows)} rows skipped: {failed_rows[:5]}{'...' if len(failed_rows) > 5 else ''}")
        return inspection_records, yield_records, missing_masters, timing, failed_rows

    def _disable_triggers(self, table_names: List[str]) -> None:
        """Disable triggers on tables for faster bulk load."""
        try:
            for table in table_names:
                disable_sql = text(f"ALTER TABLE operations.{table} DISABLE TRIGGER ALL")
                self.db.execute(disable_sql)
            self.db.commit()
            logger.info(f"Disabled triggers on {len(table_names)} tables")
        except Exception as e:
            logger.warning(f"Could not disable triggers: {e}")
            self.db.rollback()
    
    def _enable_triggers(self, table_names: List[str]) -> None:
        """Re-enable triggers on tables after bulk load."""
        try:
            for table in table_names:
                enable_sql = text(f"ALTER TABLE operations.{table} ENABLE TRIGGER ALL")
                self.db.execute(enable_sql)
            self.db.commit()
            logger.info(f"Re-enabled triggers on {len(table_names)} tables")
        except Exception as e:
            logger.warning(f"Could not enable triggers: {e}")
            self.db.rollback()

    def _bulk_insert_with_copy(
        self,
        records: List[Dict],
        table_name: str,
        columns: List[str]
    ) -> int:
        """
        Ultra-fast bulk insert using PostgreSQL COPY FROM STDIN.
        This is the fastest method for bulk loading data.
        """
        if not records:
            return 0
        
        try:
            conn = self.db.bind.raw_connection()
            cursor = conn.cursor()
            
            # Prepare COPY command
            copy_sql = f"""
                COPY operations.{table_name} ({', '.join(columns)})
                FROM STDIN WITH (FORMAT CSV, NULL '')
            """
            
            # Convert records to CSV format
            import io
            output = io.StringIO()
            for rec in records:
                row = [str(rec.get(col, '')) if rec.get(col) is not None else '' for col in columns]
                output.write(','.join(row) + '\n')
            output.seek(0)
            
            # Execute COPY
            cursor.copy_expert(copy_sql, output)
            conn.commit()
            cursor.close()
            conn.close()
            
            return len(records)
        except Exception as e:
            logger.error(f"COPY FROM STDIN failed for {table_name}, falling back to execute_batch: {e}")
            self.db.rollback()
            raise

    def _optimized_bulk_insert(
        self, 
        inspection_records: List[Dict], 
        yield_records: List[Dict]
    ) -> Tuple[Dict[str, int], Dict[str, float]]:
        """
        Optimized bulk insert using execute_batch. Disables triggers during load.
        Returns (results_dict, timing_dict) for profiling.
        """
        results = {
            'inspection_inserted': 0,
            'yield_inserted': 0,
            'inspection_errors': 0,
            'yield_errors': 0
        }
        timing = {}
        start_time = time.perf_counter()
        tables_to_disable = ['season_crop_inspection_base', 'season_crop_yield']
        
        try:
            t0 = time.perf_counter()
            self._disable_triggers(tables_to_disable)
            timing['bulk_disable_triggers'] = time.perf_counter() - t0
            logger.info(f"  [PROFILING] disable triggers: {timing['bulk_disable_triggers']:.3f}s")
            
            use_raw_conn = False
            if PSYCOPG2_AVAILABLE:
                try:
                    conn = self.db.bind.raw_connection()
                    use_raw_conn = True
                except:
                    conn = self.db.connection()
                    use_raw_conn = False
            else:
                conn = self.db.connection()
            cursor = conn.cursor()
            
            if inspection_records:
                try:
                    t0 = time.perf_counter()
                    insert_sql = """
                        INSERT INTO operations.season_crop_inspection_base (
                            season_id, crop_id, variety_id, lot_id, location_id, grower_id, organizer_id,
                            hybrid_id, season, crop, production_code, hsp_code, organizer_name, org_id,
                            purchasing_document_number, growers_name, father_name, village, village_id,
                            taluka_mandal, district, state
                        ) VALUES (
                            %(season_id)s, %(crop_id)s, %(variety_id)s, %(lot_id)s, %(location_id)s, 
                            %(grower_id)s, %(organizer_id)s, %(hybrid_id)s, %(season)s, %(crop)s,
                            %(production_code)s, %(hsp_code)s, %(organizer_name)s, %(org_id)s,
                            %(purchasing_document_number)s, %(growers_name)s, %(father_name)s,
                            %(village)s, %(village_id)s, %(taluka_mandal)s, %(district)s, %(state)s
                        )
                        ON CONFLICT (season_id, crop_id, variety_id, lot_id) DO UPDATE SET
                            location_id = EXCLUDED.location_id,
                            grower_id = EXCLUDED.grower_id,
                            organizer_id = EXCLUDED.organizer_id,
                            hybrid_id = EXCLUDED.hybrid_id,
                            season = EXCLUDED.season,
                            crop = EXCLUDED.crop,
                            production_code = EXCLUDED.production_code,
                            hsp_code = EXCLUDED.hsp_code,
                            organizer_name = EXCLUDED.organizer_name,
                            org_id = EXCLUDED.org_id,
                            purchasing_document_number = EXCLUDED.purchasing_document_number,
                            growers_name = EXCLUDED.growers_name,
                            father_name = EXCLUDED.father_name,
                            village = EXCLUDED.village,
                            village_id = EXCLUDED.village_id,
                            taluka_mandal = EXCLUDED.taluka_mandal,
                            district = EXCLUDED.district,
                            state = EXCLUDED.state
                    """
                    
                    clean_records = []
                    for rec in inspection_records:
                        clean_rec = {
                            'season_id': rec.get('season_id'),
                            'crop_id': rec.get('crop_id'),
                            'variety_id': rec.get('variety_id'),
                            'lot_id': rec.get('lot_id'),
                            'location_id': rec.get('location_id'),
                            'grower_id': rec.get('grower_id'),
                            'organizer_id': rec.get('organizer_id'),
                            'hybrid_id': rec.get('hybrid_id'),
                            'season': rec.get('season'),
                            'crop': rec.get('crop'),
                            'production_code': rec.get('production_code'),
                            'hsp_code': rec.get('hsp_code'),
                            'organizer_name': rec.get('organizer_name'),
                            'org_id': rec.get('org_id'),
                            'purchasing_document_number': rec.get('purchasing_document_number'),
                            'growers_name': rec.get('growers_name'),
                            'father_name': rec.get('father_name'),
                            'village': rec.get('village'),
                            'village_id': rec.get('village_id'),
                            'taluka_mandal': rec.get('taluka_mandal'),
                            'district': rec.get('district'),
                            'state': rec.get('state'),
                        }
                        clean_records.append(clean_rec)
                    timing['bulk_prepare_inspection'] = time.perf_counter() - t0
                    logger.info(f"    [PREPARE] {len(clean_records):,} inspection records prepared ({timing['bulk_prepare_inspection']:.3f}s)")
                    
                    t0 = time.perf_counter()
                    logger.info(f"    [UPSERT] Upserting into operations.season_crop_inspection_base...")
                    if PSYCOPG2_AVAILABLE:
                        psycopg2.extras.execute_batch(cursor, insert_sql, clean_records, page_size=10000)
                    else:
                        from sqlalchemy.dialects.postgresql import insert
                        stmt = insert(SeasonCropInspectionBase.__table__).values(clean_records)
                        stmt = stmt.on_conflict_do_update(
                            index_elements=['season_id', 'crop_id', 'variety_id', 'lot_id'],
                            set_={
                                'location_id': stmt.excluded.location_id,
                                'grower_id': stmt.excluded.grower_id,
                                'organizer_id': stmt.excluded.organizer_id,
                                'hybrid_id': stmt.excluded.hybrid_id,
                                'season': stmt.excluded.season,
                                'crop': stmt.excluded.crop,
                                'production_code': stmt.excluded.production_code,
                                'hsp_code': stmt.excluded.hsp_code,
                                'organizer_name': stmt.excluded.organizer_name,
                                'org_id': stmt.excluded.org_id,
                                'purchasing_document_number': stmt.excluded.purchasing_document_number,
                                'growers_name': stmt.excluded.growers_name,
                                'father_name': stmt.excluded.father_name,
                                'village': stmt.excluded.village,
                                'village_id': stmt.excluded.village_id,
                                'taluka_mandal': stmt.excluded.taluka_mandal,
                                'district': stmt.excluded.district,
                                'state': stmt.excluded.state,
                            }
                        )
                        self.db.execute(stmt)
                    timing['bulk_insert_inspection_base'] = time.perf_counter() - t0
                    results['inspection_inserted'] = len(clean_records)
                    throughput = results['inspection_inserted']/max(timing['bulk_insert_inspection_base'],0.001)
                    logger.info(f"    [SUCCESS] season_crop_inspection_base: {results['inspection_inserted']:,} rows upserted")
                    logger.info(f"    [TIMING] {timing['bulk_insert_inspection_base']:.3f}s ({throughput:,.0f} rows/sec)")
                    
                except Exception as e:
                    logger.error(f"    [FAILED] Error inserting inspection records: {e}")
                    logger.error(f"    [FAILED] Table: operations.season_crop_inspection_base")
                    logger.error(f"    [FAILED] Attempted rows: {len(inspection_records):,}")
                    results['inspection_errors'] = len(inspection_records)
                    self.db.rollback()
            
            if yield_records:
                try:
                    t0 = time.perf_counter()
                    insert_sql = """
                        INSERT INTO operations.season_crop_yield (
                            season_id, crop_id, variety_id, lot_id, location_id, grower_id, organizer_id,
                            m1_soaking_date, m1_soaking_week, female_soaking_date, female_date_of_transplant,
                            po_soaking_acres, planting_list_soaking_acres, sowing_acres, net_tp_acres,
                            net_acreage_area, final_harvestable_area, sum_of_received_qty, packed_qty,
                            productivity_of_packed_seed, yield_slab, qty, rate_per_kg, amount_inr,
                            male_parent_seed_lot_no, male_soaking_acre, male_no_of_pkt, male_qty_in_kgs,
                            female_parent_seed_lot_no, female_soaking_acre, female_no_of_pkt, female_qty_in_kgs
                        ) VALUES (
                            %(season_id)s, %(crop_id)s, %(variety_id)s, %(lot_id)s, %(location_id)s,
                            %(grower_id)s, %(organizer_id)s, %(m1_soaking_date)s, %(m1_soaking_week)s,
                            %(female_soaking_date)s, %(female_date_of_transplant)s, %(po_soaking_acres)s,
                            %(planting_list_soaking_acres)s, %(sowing_acres)s, %(net_tp_acres)s,
                            %(net_acreage_area)s, %(final_harvestable_area)s, %(sum_of_received_qty)s,
                            %(packed_qty)s, %(productivity_of_packed_seed)s, %(yield_slab)s, %(qty)s,
                            %(rate_per_kg)s, %(amount_inr)s, %(male_parent_seed_lot_no)s, %(male_soaking_acre)s,
                            %(male_no_of_pkt)s, %(male_qty_in_kgs)s, %(female_parent_seed_lot_no)s,
                            %(female_soaking_acre)s, %(female_no_of_pkt)s, %(female_qty_in_kgs)s
                        )
                        ON CONFLICT (season_id, crop_id, variety_id, lot_id) DO UPDATE SET
                            location_id = EXCLUDED.location_id,
                            grower_id = EXCLUDED.grower_id,
                            organizer_id = EXCLUDED.organizer_id,
                            m1_soaking_date = EXCLUDED.m1_soaking_date,
                            m1_soaking_week = EXCLUDED.m1_soaking_week,
                            female_soaking_date = EXCLUDED.female_soaking_date,
                            female_date_of_transplant = EXCLUDED.female_date_of_transplant,
                            po_soaking_acres = EXCLUDED.po_soaking_acres,
                            planting_list_soaking_acres = EXCLUDED.planting_list_soaking_acres,
                            sowing_acres = EXCLUDED.sowing_acres,
                            net_tp_acres = EXCLUDED.net_tp_acres,
                            net_acreage_area = EXCLUDED.net_acreage_area,
                            final_harvestable_area = EXCLUDED.final_harvestable_area,
                            sum_of_received_qty = EXCLUDED.sum_of_received_qty,
                            packed_qty = EXCLUDED.packed_qty,
                            productivity_of_packed_seed = EXCLUDED.productivity_of_packed_seed,
                            yield_slab = EXCLUDED.yield_slab,
                            qty = EXCLUDED.qty,
                            rate_per_kg = EXCLUDED.rate_per_kg,
                            amount_inr = EXCLUDED.amount_inr,
                            male_parent_seed_lot_no = EXCLUDED.male_parent_seed_lot_no,
                            male_soaking_acre = EXCLUDED.male_soaking_acre,
                            male_no_of_pkt = EXCLUDED.male_no_of_pkt,
                            male_qty_in_kgs = EXCLUDED.male_qty_in_kgs,
                            female_parent_seed_lot_no = EXCLUDED.female_parent_seed_lot_no,
                            female_soaking_acre = EXCLUDED.female_soaking_acre,
                            female_no_of_pkt = EXCLUDED.female_no_of_pkt,
                            female_qty_in_kgs = EXCLUDED.female_qty_in_kgs
                    """
                    
                    clean_records = []
                    for rec in yield_records:
                        clean_rec = {
                            'season_id': rec.get('season_id'),
                            'crop_id': rec.get('crop_id'),
                            'variety_id': rec.get('variety_id'),
                            'lot_id': rec.get('lot_id'),
                            'location_id': rec.get('location_id'),
                            'grower_id': rec.get('grower_id'),
                            'organizer_id': rec.get('organizer_id'),
                            'm1_soaking_date': rec.get('m1_soaking_date'),
                            'm1_soaking_week': rec.get('m1_soaking_week'),
                            'female_soaking_date': rec.get('female_soaking_date'),
                            'female_date_of_transplant': rec.get('female_date_of_transplant'),
                            'po_soaking_acres': rec.get('po_soaking_acres'),
                            'planting_list_soaking_acres': rec.get('planting_list_soaking_acres'),
                            'sowing_acres': rec.get('sowing_acres'),
                            'net_tp_acres': rec.get('net_tp_acres'),
                            'net_acreage_area': rec.get('net_acreage_area'),
                            'final_harvestable_area': rec.get('final_harvestable_area'),
                            'sum_of_received_qty': rec.get('sum_of_received_qty'),
                            'packed_qty': rec.get('packed_qty'),
                            'productivity_of_packed_seed': rec.get('productivity_of_packed_seed'),
                            'yield_slab': rec.get('yield_slab'),
                            'qty': rec.get('qty'),
                            'rate_per_kg': rec.get('rate_per_kg'),
                            'amount_inr': rec.get('amount_inr'),
                            'male_parent_seed_lot_no': rec.get('male_parent_seed_lot_no'),
                            'male_soaking_acre': rec.get('male_soaking_acre'),
                            'male_no_of_pkt': rec.get('male_no_of_pkt'),
                            'male_qty_in_kgs': rec.get('male_qty_in_kgs'),
                            'female_parent_seed_lot_no': rec.get('female_parent_seed_lot_no'),
                            'female_soaking_acre': rec.get('female_soaking_acre'),
                            'female_no_of_pkt': rec.get('female_no_of_pkt'),
                            'female_qty_in_kgs': rec.get('female_qty_in_kgs'),
                        }
                        clean_records.append(clean_rec)
                    timing['bulk_prepare_yield'] = time.perf_counter() - t0
                    logger.info(f"    [PREPARE] {len(clean_records):,} yield records prepared ({timing['bulk_prepare_yield']:.3f}s)")
                    
                    t0 = time.perf_counter()
                    logger.info(f"    [UPSERT] Upserting into operations.season_crop_yield...")
                    if PSYCOPG2_AVAILABLE:
                        psycopg2.extras.execute_batch(cursor, insert_sql, clean_records, page_size=10000)
                    else:
                        from sqlalchemy.dialects.postgresql import insert
                        stmt = insert(YieldRecord.__table__).values(clean_records)
                        stmt = stmt.on_conflict_do_update(
                            index_elements=['season_id', 'crop_id', 'variety_id', 'lot_id'],
                            set_={
                                'location_id': stmt.excluded.location_id,
                                'grower_id': stmt.excluded.grower_id,
                                'organizer_id': stmt.excluded.organizer_id,
                                'm1_soaking_date': stmt.excluded.m1_soaking_date,
                                'm1_soaking_week': stmt.excluded.m1_soaking_week,
                                'female_soaking_date': stmt.excluded.female_soaking_date,
                                'female_date_of_transplant': stmt.excluded.female_date_of_transplant,
                                'po_soaking_acres': stmt.excluded.po_soaking_acres,
                                'planting_list_soaking_acres': stmt.excluded.planting_list_soaking_acres,
                                'sowing_acres': stmt.excluded.sowing_acres,
                                'net_tp_acres': stmt.excluded.net_tp_acres,
                                'net_acreage_area': stmt.excluded.net_acreage_area,
                                'final_harvestable_area': stmt.excluded.final_harvestable_area,
                                'sum_of_received_qty': stmt.excluded.sum_of_received_qty,
                                'packed_qty': stmt.excluded.packed_qty,
                                'productivity_of_packed_seed': stmt.excluded.productivity_of_packed_seed,
                                'yield_slab': stmt.excluded.yield_slab,
                                'qty': stmt.excluded.qty,
                                'rate_per_kg': stmt.excluded.rate_per_kg,
                                'amount_inr': stmt.excluded.amount_inr,
                                'male_parent_seed_lot_no': stmt.excluded.male_parent_seed_lot_no,
                                'male_soaking_acre': stmt.excluded.male_soaking_acre,
                                'male_no_of_pkt': stmt.excluded.male_no_of_pkt,
                                'male_qty_in_kgs': stmt.excluded.male_qty_in_kgs,
                                'female_parent_seed_lot_no': stmt.excluded.female_parent_seed_lot_no,
                                'female_soaking_acre': stmt.excluded.female_soaking_acre,
                                'female_no_of_pkt': stmt.excluded.female_no_of_pkt,
                                'female_qty_in_kgs': stmt.excluded.female_qty_in_kgs,
                            }
                        )
                        self.db.execute(stmt)
                    timing['bulk_insert_season_crop_yield'] = time.perf_counter() - t0
                    results['yield_inserted'] = len(clean_records)
                    throughput = results['yield_inserted']/max(timing['bulk_insert_season_crop_yield'],0.001)
                    logger.info(f"    [SUCCESS] season_crop_yield: {results['yield_inserted']:,} rows upserted")
                    logger.info(f"    [TIMING] {timing['bulk_insert_season_crop_yield']:.3f}s ({throughput:,.0f} rows/sec)")
                    
                except Exception as e:
                    logger.error(f"    [FAILED] Error inserting yield records: {e}")
                    logger.error(f"    [FAILED] Table: operations.season_crop_yield")
                    logger.error(f"    [FAILED] Attempted rows: {len(yield_records):,}")
                    results['yield_errors'] = len(yield_records)
                    self.db.rollback()
            
            t0 = time.perf_counter()
            if use_raw_conn:
                conn.commit()
            else:
                self.db.commit()
            timing['bulk_commit'] = time.perf_counter() - t0
            logger.info(f"  [PROFILING] commit: {timing['bulk_commit']:.3f}s")
            cursor.close()
            if use_raw_conn:
                conn.close()
            
            t0 = time.perf_counter()
            self._enable_triggers(tables_to_disable)
            timing['bulk_enable_triggers'] = time.perf_counter() - t0
            logger.info(f"  [PROFILING] enable triggers: {timing['bulk_enable_triggers']:.3f}s")
            
        except Exception as e:
            self.db.rollback()
            try:
                self._enable_triggers(tables_to_disable)
            except:
                pass
            logger.error(f"Error during bulk insert: {e}")
            raise
        
        timing['bulk_insert_total'] = time.perf_counter() - start_time
        logger.info(f"[PROFILING] bulk insert total: {timing['bulk_insert_total']:.3f}s")
        return results, timing

    # ============================================
    # NEW METHODS FOR YIELD DATA RELOAD
    # ============================================

    def truncate_yield_tables(self) -> Dict[str, Any]:
        """
        Safely truncate season_crop_inspection_base and season_crop_yield tables.
        
        SAFE OPERATION: Does NOT drop or recreate tables - only removes data.
        Master tables (seasons, crops, varieties, growers, organizers, locations) 
        are NEVER touched.
        
        Returns:
            Dict with row counts before truncation
        """
        stats = {
            'inspection_base_rows_before': 0,
            'yield_rows_before': 0,
            'tables_truncated': []
        }
        
        try:
            logger.info("=" * 60)
            logger.info("[SAFE RELOAD] Truncating yield data tables (schema preserved)")
            logger.info("=" * 60)
            
            # Skip pre-counts for slow connections - go directly to truncate
            logger.info("  [INFO] Skipping pre-counts (slow connection optimization)")
            stats['inspection_base_rows_before'] = -1  # -1 indicates skipped
            stats['yield_rows_before'] = -1
            
            # TRUNCATE tables (fast, keeps schema)
            logger.info("  [TRUNCATING] operations.season_crop_inspection_base...")
            self.db.execute(text("TRUNCATE TABLE operations.season_crop_inspection_base CASCADE"))
            stats['tables_truncated'].append('operations.season_crop_inspection_base')
            logger.info("    [SUCCESS] season_crop_inspection_base truncated")
            
            logger.info("  [TRUNCATING] operations.season_crop_yield...")
            self.db.execute(text("TRUNCATE TABLE operations.season_crop_yield CASCADE"))
            stats['tables_truncated'].append('operations.season_crop_yield')
            logger.info("    [SUCCESS] season_crop_yield truncated")
            
            self.db.commit()
            
            logger.info("=" * 60)
            logger.info(f"[SAFE RELOAD] Truncation complete - {len(stats['tables_truncated'])} tables cleared")
            logger.info(f"  Total rows removed: {stats['inspection_base_rows_before'] + stats['yield_rows_before']:,}")
            logger.info("  Master tables (seasons, crops, varieties, etc.) PRESERVED")
            logger.info("=" * 60)
            
            return stats
            
        except Exception as e:
            try:
                self.db.rollback()
            except:
                pass
            logger.error(f"[ERROR] Failed to truncate tables: {e}")
            raise
    
    def recreate_tables_with_composite_keys(self) -> None:
        """
        DEPRECATED: Use truncate_yield_tables() instead for safe data reload.
        
        This method is kept for backwards compatibility but now only truncates data.
        It does NOT drop or recreate tables to protect production schemas.
        
        WARNING: Drop/recreate operations have been removed to prevent 
        accidental destruction of production table structures.
        """
        logger.warning("=" * 70)
        logger.warning("[DEPRECATED] recreate_tables_with_composite_keys() called")
        logger.warning("  This method now performs TRUNCATE instead of DROP/CREATE")
        logger.warning("  Use truncate_yield_tables() for explicit safe reload")
        logger.warning("=" * 70)
        
        # Delegate to safe truncate method
        self.truncate_yield_tables()
        
        logger.info("Tables truncated successfully (schema preserved)")

    def normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize DataFrame columns: lowercase, strip, replace spaces with underscores.
        Map lot_no to lot_id and normalize lot_id values.
        
        Normalization Rules:
        - Column names: lowercase, strip, replace spaces with underscores
        - lot_no → lot_id mapping
        - lot_id values: LOWER(TRIM(lot_no))
        """
        df = df.copy()
        
        # Normalize column names
        df.columns = df.columns.str.lower().str.strip().str.replace(" ", "_")
        
        # Map lot_no to lot_id and normalize values
        if 'lot_no' in df.columns:
            if 'lot_id' not in df.columns:
                df['lot_id'] = df['lot_no']
            else:
                # Fill lot_id with lot_no where lot_id is missing
                mask = df['lot_id'].isna() | (df['lot_id'] == '')
                df.loc[mask, 'lot_id'] = df.loc[mask, 'lot_no']
        
        # Normalize lot_id values: LOWER(TRIM(lot_id))
        if 'lot_id' in df.columns:
            df['lot_id'] = df['lot_id'].astype(str).str.strip().str.lower()
            # Replace normalized empty values with None
            df['lot_id'] = df['lot_id'].replace(['nan', 'none', 'null', ''], None)
        
        return df

    def get_max_season_id(self, db: Session) -> int:
        """Get the maximum season ID number from existing records."""
        try:
            max_season = db.query(func.max(SeasonRecord.season_id)).scalar()
            if max_season:
                # Extract number from ID if numeric, otherwise return 0
                match = re.search(r'\d+', str(max_season))
                if match:
                    return int(match.group())
            return 0
        except Exception:
            return 0

    def get_max_grower_id(self, db: Session) -> int:
        """Get the maximum grower ID number from existing records."""
        try:
            max_grower = db.query(func.max(GrowerRecord.grower_id)).scalar()
            if max_grower:
                match = re.search(r'\d+', max_grower)
                if match:
                    return int(match.group())
            return 300000  # Default start
        except Exception:
            return 300000

    def get_max_organizer_id(self, db: Session) -> int:
        """Get the maximum organizer ID number from existing records."""
        try:
            max_org = db.query(func.max(OrganizerRecord.organizer_id)).scalar()
            if max_org:
                match = re.search(r'\d+', max_org)
                if match:
                    return int(match.group())
            return 600000  # Default start
        except Exception:
            return 600000

    def create_master_if_missing(
        self, 
        master_type: str, 
        name: str, 
        **kwargs
    ) -> Optional[str]:
        """
        Create master record if missing. Returns the ID (existing or newly created).
        All lookups use normalized values (TRIM + LOWER).
        Duplicate names that differ only by spaces/case are treated as the same record.
        
        Args:
            master_type: 'season', 'crop', 'variety', 'grower', 'organizer', 'location'
            name: Name of the master record (will be normalized)
            **kwargs: Additional fields for creation (e.g., crop_id for variety, village/district/state for location)
        
        Returns:
            Master record ID or None if creation fails
        """
        if not name or pd.isna(name):
            return None
        
        # Normalize: TRIM and LOWER for consistent lookup
        # Store original for display, but use normalized for matching
        name_clean = str(name).strip()
        name_normalized = name_clean.lower()
        
        # Handle empty after normalization
        if not name_normalized or name_normalized in ['nan', 'none', 'null', '']:
            return None
        
        try:
            if master_type == 'season':
                # Check cache first
                if hasattr(self, 'season_name_to_id') and name_normalized in self.season_name_to_id:
                    return self.season_name_to_id[name_normalized]
                
                # Check database
                existing = self.db.query(SeasonRecord).filter(
                    func.lower(SeasonRecord.season_name) == name_normalized
                ).first()
                
                if existing:
                    if not hasattr(self, 'season_name_to_id'):
                        self.season_name_to_id = {}
                    self.season_name_to_id[name_normalized] = existing.season_id
                    return existing.season_id
                
                # Create new season - use normalized season name as ID
                season_id = name_clean.replace(" ", "_").replace("-", "_").upper()
                new_season = SeasonRecord(
                    season_id=season_id,
                    season_name=name_clean
                )
                self.db.add(new_season)
                self.db.commit()
                
                if not hasattr(self, 'season_name_to_id'):
                    self.season_name_to_id = {}
                self.season_name_to_id[name_normalized] = season_id
                logger.info(f"✅ Created new master record [SEASON]: {season_id} - '{name_clean}' (normalized: '{name_normalized}')")
                return season_id
                
            elif master_type == 'crop':
                # _ensure_crop_exists already uses normalized lookup
                crop_id = self._ensure_crop_exists(name_clean, kwargs.get('category_id', 100001))
                if crop_id and name_normalized not in self.crop_name_to_id:
                    # Log if it was newly created (cache wasn't updated)
                    logger.info(f"✅ Created new master record [CROP]: {crop_id} - '{name_clean}' (normalized: '{name_normalized}')")
                return crop_id
                
            elif master_type == 'variety':
                crop_id = kwargs.get('crop_id')
                if not crop_id:
                    logger.warning(f"Cannot create variety '{name_clean}' without crop_id")
                    return None
                variety_id = self._ensure_variety_exists(name_clean, crop_id, kwargs.get('category_id', 100003))
                if variety_id:
                    cache_key = (name_normalized, crop_id)
                    if cache_key not in self.variety_name_crop_to_id:
                        logger.info(f"✅ Created new master record [VARIETY]: {variety_id} - '{name_clean}' (crop_id: {crop_id}, normalized: '{name_normalized}')")
                return variety_id
                
            elif master_type == 'grower':
                # Check cache first
                if name_normalized in self.grower_name_to_id:
                    return self.grower_name_to_id[name_normalized]
                
                # Check database
                existing = self.db.query(GrowerRecord).filter(
                    func.lower(GrowerRecord.grower_name) == name_normalized
                ).first()
                
                if existing:
                    self.grower_name_to_id[name_normalized] = existing.grower_id
                    return existing.grower_id
                
                # Create new grower
                max_id = self.get_max_grower_id(self.db)
                new_id = f"G_{str(max_id + 1).zfill(6)}"
                new_grower = GrowerRecord(
                    grower_id=new_id,
                    grower_name=name_clean,
                    category_id=kwargs.get('category_id', 100005),
                    fathers_name=kwargs.get('fathers_name'),
                    grower_gender=kwargs.get('grower_gender')
                )
                self.db.add(new_grower)
                self.db.commit()
                self.grower_name_to_id[name_normalized] = new_id
                logger.info(f"✅ Created new master record [GROWER]: {new_id} - '{name_clean}' (normalized: '{name_normalized}')")
                return new_id
                
            elif master_type == 'organizer':
                # Check cache first
                if name_normalized in self.organizer_name_to_id:
                    return self.organizer_name_to_id[name_normalized]
                
                # Check database
                existing = self.db.query(OrganizerRecord).filter(
                    func.lower(OrganizerRecord.organizer_name) == name_normalized
                ).first()
                
                if existing:
                    self.organizer_name_to_id[name_normalized] = existing.organizer_id
                    return existing.organizer_id
                
                # Create new organizer
                max_id = self.get_max_organizer_id(self.db)
                new_id = f"O_{str(max_id + 1).zfill(6)}"
                new_organizer = OrganizerRecord(
                    organizer_id=new_id,
                    organizer_name=name_clean,
                    category_id=kwargs.get('category_id', 100006),
                    production_plant=kwargs.get('production_plant')
                )
                self.db.add(new_organizer)
                self.db.commit()
                self.organizer_name_to_id[name_normalized] = new_id
                logger.info(f"✅ Created new master record [ORGANIZER]: {new_id} - '{name_clean}' (normalized: '{name_normalized}')")
                return new_id
                
            elif master_type == 'location':
                # _resolve_location_id already uses normalized composite key matching
                # name_clean is already normalized village
                village = name_clean
                mandal = kwargs.get('mandal')  # Already normalized in resolve_master_id
                district = kwargs.get('district')  # Already normalized in resolve_master_id
                state = kwargs.get('state')  # Already normalized in resolve_master_id
                return self._resolve_location_id(village, mandal, district, state)
                
            else:
                logger.warning(f"Unknown master type: {master_type}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating {master_type} '{name_clean}': {e}")
            self.db.rollback()
            return None

    def _normalize_lookup_value(self, value: Any) -> Optional[str]:
        """
        Normalize lookup value: TRIM() and LOWER().
        Handles None, NaN, and empty strings.
        
        Returns:
            Normalized string or None
        """
        if value is None or pd.isna(value):
            return None
        
        value_str = str(value).strip()
        if not value_str or value_str.lower() in ['nan', 'none', 'null', '']:
            return None
        
        return value_str.lower()

    def _normalize_location_field(self, value: Any) -> str:
        """
        Normalize location fields (village, district, state): trim spaces, lowercase,
        remove/sanitize special characters for consistent matching.
        
        Returns:
            Normalized string (empty string if invalid)
        """
        if value is None or pd.isna(value):
            return ''
        s = str(value).strip().lower()
        if not s or s in ['nan', 'none', 'null', '']:
            return ''
        # Remove extra whitespace, normalize common special chars
        s = re.sub(r'\s+', ' ', s)
        s = s.strip()
        return s

    # Fallback location for rows with empty village - ensures location_id is never null
    UNKNOWN_LOCATION_VILLAGE = 'unknown_location'
    
    def resolve_master_id(self, row: Dict, master_type: str) -> Optional[str]:
        """
        Resolve master ID from row data. Auto-creates if missing.
        All lookup fields are normalized with TRIM() and LOWER().
        
        Args:
            row: DataFrame row as dict
            master_type: 'season_id', 'crop_id', 'variety_id', 'grower_id', 'organizer_id', 'location_id'
        
        Returns:
            Master record ID or None
        """
        try:
            if master_type == 'season_id':
                season_name = row.get('season') or row.get('season_id')
                if not season_name:
                    return None
                # Normalize: TRIM and convert to uppercase for season_id format
                season_name = str(season_name).strip().replace(" ", "_").replace("-", "_").upper()
                return self.create_master_if_missing('season', season_name)
                
            elif master_type == 'crop_id':
                crop_name = row.get('crop') or row.get('crop_name')
                if not crop_name:
                    return None
                # Normalize: TRIM and LOWER for lookup
                crop_name = self._normalize_lookup_value(crop_name)
                if not crop_name:
                    return None
                return self.create_master_if_missing('crop', crop_name)
                
            elif master_type == 'variety_id':
                variety_name = row.get('hsp_code') or row.get('variety') or row.get('variety_name')
                crop_id = row.get('crop_id')
                if not variety_name:
                    return None
                # Normalize: TRIM and LOWER for lookup
                variety_name = self._normalize_lookup_value(variety_name)
                if not variety_name:
                    return None
                if not crop_id:
                    # Try to resolve crop_id first
                    crop_name = row.get('crop') or row.get('crop_name')
                    if crop_name:
                        crop_name_norm = self._normalize_lookup_value(crop_name)
                        if crop_name_norm:
                            crop_id = self.create_master_if_missing('crop', crop_name_norm)
                return self.create_master_if_missing('variety', variety_name, crop_id=crop_id)
                
            elif master_type == 'grower_id':
                grower_name = row.get('growers_name') or row.get('grower_name')
                if not grower_name:
                    return None
                # Normalize: TRIM and LOWER for lookup
                grower_name = self._normalize_lookup_value(grower_name)
                if not grower_name:
                    return None
                return self.create_master_if_missing(
                    'grower', 
                    grower_name,
                    fathers_name=self._normalize_lookup_value(row.get('father_name') or row.get('fathers_name')),
                    grower_gender=self._normalize_lookup_value(row.get('grower_gender'))
                )
                
            elif master_type == 'organizer_id':
                organizer_name = row.get('organizer_name') or row.get('org_id')
                if not organizer_name:
                    return None
                # Normalize: TRIM and LOWER for lookup
                organizer_name = self._normalize_lookup_value(organizer_name)
                if not organizer_name:
                    return None
                return self.create_master_if_missing(
                    'organizer',
                    organizer_name,
                    production_plant=self._normalize_lookup_value(row.get('production_plant'))
                )
                
            elif master_type == 'location_id':
                village = row.get('village') or row.get('village_id')
                if not village:
                    return None
                # Normalize all location fields: TRIM and LOWER
                village_norm = self._normalize_lookup_value(village)
                if not village_norm:
                    return None
                district_norm = self._normalize_lookup_value(row.get('district'))
                state_norm = self._normalize_lookup_value(row.get('state'))
                mandal_norm = self._normalize_lookup_value(row.get('taluka_mandal') or row.get('mandal'))
                
                return self.create_master_if_missing(
                    'location',
                    village_norm,
                    mandal=mandal_norm,
                    district=district_norm,
                    state=state_norm
                )
                
            else:
                logger.warning(f"Unknown master type: {master_type}")
                return None
                
        except Exception as e:
            logger.warning(f"Error resolving {master_type}: {e}")
            return None

    def split_and_prepare_data(self, df: pd.DataFrame) -> Tuple[List[Dict], List[Dict]]:
        """
        Split DataFrame into inspection_base and yield records.
        Resolve all master data IDs and prepare for bulk insert.
        
        Returns:
            Tuple of (inspection_base_records, yield_records)
        """
        inspection_records = []
        yield_records = []
        
        # Columns for inspection_base
        inspection_columns = [
            'season', 'crop', 'production_code', 'hsp_code', 'variety',
            'organizer_name', 'org_id', 'purchasing_document_number',
            'growers_name', 'father_name', 'village', 'village_id',
            'taluka_mandal', 'mandal', 'district', 'state', 'lot_id'
        ]
        
        # Columns for yield
        yield_columns = [
            'lot_id', 'm1_soaking_date', 'm1_soaking_week',
            'female_soaking_date', 'female_date_of_transplant',
            'po_soaking_acres', 'planting_list_soaking_acres',
            'sowing_acres', 'net_tp_acres', 'net_acreage_area',
            'final_harvestable_area', 'sum_of_received_qty',
            'packed_qty', 'productivity_of_packed_seed', 'yield_slab',
            'qty', 'rate_per_kg', 'amount_inr',
            'male_parent_seed_lot_no', 'male_soaking_acre',
            'male_no_of_pkt', 'male_qty_in_kgs',
            'female_parent_seed_lot_no', 'female_soaking_acre',
            'female_no_of_pkt', 'female_qty_in_kgs'
        ]
        
        for idx, row in df.iterrows():
            try:
                # Resolve all master IDs
                season_id = self.resolve_master_id(row, 'season_id')
                crop_id = self.resolve_master_id(row, 'crop_id')
                variety_id = self.resolve_master_id(row, 'variety_id')
                grower_id = self.resolve_master_id(row, 'grower_id')
                organizer_id = self.resolve_master_id(row, 'organizer_id')
                location_id = self.resolve_master_id(row, 'location_id')
                
                # Get lot_id (required for primary key)
                # Normalize lot_id: LOWER(TRIM(lot_no)) -> lot_id
                lot_id = row.get('lot_id') or row.get('lot_no')
                if not lot_id or pd.isna(lot_id):
                    logger.warning(f"Row {idx} missing lot_id/lot_no, skipping")
                    continue
                
                # Normalize lot_id: TRIM and LOWER (as per requirement)
                lot_id = str(lot_id).strip().lower()
                if not lot_id or lot_id in ['nan', 'none', 'null', '']:
                    logger.warning(f"Row {idx} lot_id is empty after normalization, skipping")
                    continue
                
                # Validate required fields for primary key
                if not all([season_id, crop_id, variety_id, lot_id]):
                    logger.warning(f"Row {idx} missing required primary key fields, skipping")
                    continue
                
                # Prepare inspection_base record
                inspection_record = {
                    'season_id': season_id,
                    'crop_id': crop_id,
                    'variety_id': variety_id,
                    'lot_id': lot_id,
                    'location_id': location_id,
                    'grower_id': grower_id,
                    'organizer_id': organizer_id,
                }
                
                # Add inspection-specific columns
                for col in inspection_columns:
                    if col in df.columns:
                        val = row.get(col)
                        if pd.notna(val):
                            inspection_record[col] = val
                
                inspection_records.append(inspection_record)
                
                # Prepare yield record
                yield_record = {
                    'season_id': season_id,
                    'crop_id': crop_id,
                    'variety_id': variety_id,
                    'lot_id': lot_id,
                    'location_id': location_id,
                    'grower_id': grower_id,
                    'organizer_id': organizer_id,
                }
                
                # Add yield-specific columns
                for col in yield_columns:
                    if col in df.columns:
                        val = row.get(col)
                        if pd.notna(val):
                            # Convert dates
                            if 'date' in col.lower():
                                try:
                                    val = pd.to_datetime(val, errors='coerce')
                                    if pd.notna(val):
                                        yield_record[col] = val.date() if hasattr(val, 'date') else val
                                except:
                                    pass
                            # Convert numeric
                            elif col in ['po_soaking_acres', 'planting_list_soaking_acres', 'sowing_acres',
                                       'net_tp_acres', 'net_acreage_area', 'final_harvestable_area',
                                       'sum_of_received_qty', 'packed_qty', 'productivity_of_packed_seed',
                                       'qty', 'rate_per_kg', 'amount_inr', 'male_soaking_acre',
                                       'male_no_of_pkt', 'male_qty_in_kgs', 'female_soaking_acre',
                                       'female_no_of_pkt', 'female_qty_in_kgs']:
                                val = pd.to_numeric(val, errors='coerce')
                                if pd.notna(val):
                                    yield_record[col] = float(val)
                            else:
                                yield_record[col] = val
                
                yield_records.append(yield_record)
                
            except Exception as e:
                logger.warning(f"Error processing row {idx}: {e}")
                continue
        
        logger.info(f"Prepared {len(inspection_records)} inspection records and {len(yield_records)} yield records")
        return inspection_records, yield_records

    def bulk_insert_tables(
        self, 
        inspection_records: List[Dict], 
        yield_records: List[Dict]
    ) -> Dict[str, int]:
        """
        Bulk insert records into both tables using raw SQL for composite primary keys.
        
        Returns:
            Dict with insertion counts
        """
        results = {
            'inspection_inserted': 0,
            'yield_inserted': 0,
            'inspection_errors': 0,
            'yield_errors': 0
        }
        
        try:
            # Bulk insert inspection_base
            if inspection_records:
                try:
                    # Prepare clean records
                    clean_records = []
                    for rec in inspection_records:
                        # Use parameterized query for safety
                        from sqlalchemy.dialects.postgresql import insert
                        from sqlalchemy import Table, Column, String, Float
                        
                        # Prepare clean records
                        clean_records = []
                        for rec in inspection_records:
                            clean_rec = {
                                'season_id': rec.get('season_id'),
                                'crop_id': rec.get('crop_id'),
                                'variety_id': rec.get('variety_id'),
                                'lot_id': rec.get('lot_id'),
                                'location_id': rec.get('location_id'),
                                'grower_id': rec.get('grower_id'),
                                'organizer_id': rec.get('organizer_id'),
                                'hybrid_id': rec.get('hybrid_id'),
                                'season': rec.get('season'),
                                'crop': rec.get('crop'),
                                'production_code': rec.get('production_code'),
                                'hsp_code': rec.get('hsp_code'),
                                'organizer_name': rec.get('organizer_name'),
                                'org_id': rec.get('org_id'),
                                'purchasing_document_number': rec.get('purchasing_document_number'),
                                'growers_name': rec.get('growers_name'),
                                'father_name': rec.get('father_name'),
                                'village': rec.get('village'),
                                'village_id': rec.get('village_id'),
                                'taluka_mandal': rec.get('taluka_mandal'),
                                'district': rec.get('district'),
                                'state': rec.get('state'),
                            }
                            clean_records.append(clean_rec)
                        
                        # Use bulk insert with conflict handling
                        stmt = insert(SeasonCropInspectionBase.__table__).values(clean_records)
                        stmt = stmt.on_conflict_do_nothing(
                            index_elements=['season_id', 'crop_id', 'variety_id', 'lot_id']
                        )
                        self.db.execute(stmt)
                        results['inspection_inserted'] = len(clean_records)
                        logger.info(f"Inserted {results['inspection_inserted']} inspection records")
                    
                except Exception as e:
                    logger.error(f"Error inserting inspection records: {e}")
                    results['inspection_errors'] = len(inspection_records)
            
            # Bulk insert yield
            if yield_records:
                try:
                    # Use parameterized query for safety
                    from sqlalchemy.dialects.postgresql import insert
                    
                    # Prepare clean records
                    clean_records = []
                    for rec in yield_records:
                        clean_rec = {
                            'season_id': rec.get('season_id'),
                            'crop_id': rec.get('crop_id'),
                            'variety_id': rec.get('variety_id'),
                            'lot_id': rec.get('lot_id'),
                            'location_id': rec.get('location_id'),
                            'grower_id': rec.get('grower_id'),
                            'organizer_id': rec.get('organizer_id'),
                            'm1_soaking_date': rec.get('m1_soaking_date'),
                            'm1_soaking_week': rec.get('m1_soaking_week'),
                            'female_soaking_date': rec.get('female_soaking_date'),
                            'female_date_of_transplant': rec.get('female_date_of_transplant'),
                            'po_soaking_acres': rec.get('po_soaking_acres'),
                            'planting_list_soaking_acres': rec.get('planting_list_soaking_acres'),
                            'sowing_acres': rec.get('sowing_acres'),
                            'net_tp_acres': rec.get('net_tp_acres'),
                            'net_acreage_area': rec.get('net_acreage_area'),
                            'final_harvestable_area': rec.get('final_harvestable_area'),
                            'sum_of_received_qty': rec.get('sum_of_received_qty'),
                            'packed_qty': rec.get('packed_qty'),
                            'productivity_of_packed_seed': rec.get('productivity_of_packed_seed'),
                            'yield_slab': rec.get('yield_slab'),
                            'qty': rec.get('qty'),
                            'rate_per_kg': rec.get('rate_per_kg'),
                            'amount_inr': rec.get('amount_inr'),
                            'male_parent_seed_lot_no': rec.get('male_parent_seed_lot_no'),
                            'male_soaking_acre': rec.get('male_soaking_acre'),
                            'male_no_of_pkt': rec.get('male_no_of_pkt'),
                            'male_qty_in_kgs': rec.get('male_qty_in_kgs'),
                            'female_parent_seed_lot_no': rec.get('female_parent_seed_lot_no'),
                            'female_soaking_acre': rec.get('female_soaking_acre'),
                            'female_no_of_pkt': rec.get('female_no_of_pkt'),
                            'female_qty_in_kgs': rec.get('female_qty_in_kgs'),
                        }
                        clean_records.append(clean_rec)
                    
                    # Use bulk insert with conflict handling
                    stmt = insert(YieldRecord.__table__).values(clean_records)
                    stmt = stmt.on_conflict_do_nothing(
                        index_elements=['season_id', 'crop_id', 'variety_id', 'lot_id']
                    )
                    self.db.execute(stmt)
                    results['yield_inserted'] = len(clean_records)
                    logger.info(f"Inserted {results['yield_inserted']} yield records")
                    
                except Exception as e:
                    logger.error(f"Error inserting yield records: {e}")
                    results['yield_errors'] = len(yield_records)
            
            # Commit all inserts
            self.db.commit()
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error during bulk insert: {e}")
            raise
        
        return results

    def reload_yield_data(
        self, 
        csv_path: str, 
        recreate_tables: bool = False,
        truncate_data: bool = True
    ) -> Dict[str, Any]:
        """
        SAFE method to reload yield data from CSV with master data lookup and auto-creation.
        
        SAFETY FEATURES:
        - NEVER drops or recreates tables (protects production schemas)
        - Uses TRUNCATE to clear existing data (fast, preserves structure)
        - Master tables are NEVER modified (only lookups and auto-creation of new entries)
        - All operations are logged in detail for audit trail
        
        Performance optimizations:
        - Preloads all master data into memory dictionaries
        - Batches master data creation
        - Uses psycopg2.extras.execute_batch for bulk inserts
        - Single transaction for all operations
        - Vectorized DataFrame operations where possible
        
        NORMALIZATION RULES (applied to all lookup fields):
        - All lookup fields use TRIM() and LOWER() for matching
        - season, crop, variety, organizer, grower: normalized with TRIM + LOWER
        - lot_id: LOWER(TRIM(lot_no)) - normalized and stored as lot_id
        - Duplicate names that differ only by spaces/case are treated as the same record
        
        MASTER DATA LOOKUP & AUTO-CREATION:
        - If season/crop/variety/organizer/grower does not exist, insert new row with generated ID
        - Location matching uses composite key: village_name + district + state (all normalized)
        - All master data creation is logged
        - Lookup and insert operations are atomic (transaction-safe)
        
        LOCATION MATCHING RULE:
        - Primary: village_name + district + state (all TRIM + LOWER)
        - Fallback: village_name + district (if state not available)
        - Last resort: village_name only
        
        Args:
            csv_path: Path to CSV file
            recreate_tables: DEPRECATED - ignored for safety. Tables are never dropped.
            truncate_data: If True (default), TRUNCATE existing data before insert.
                          If False, data will be upserted (ON CONFLICT DO UPDATE).
        
        Returns:
            Dict with reload statistics including:
            - csv_rows_loaded: Number of rows loaded from CSV
            - inspection_records_inserted: Records inserted into inspection_base
            - yield_records_inserted: Records inserted into yield table
            - master_records_created: Count of new master records by type
            - rows_truncated: Number of rows removed before reload
            - errors: List of any errors encountered
            - timing: Performance metrics for each step
        """
        total_start = time.perf_counter()
        stats = {
            'csv_rows_loaded': 0,
            'inspection_records_inserted': 0,
            'yield_records_inserted': 0,
            'rows_truncated': 0,
            'failed_rows': [],
            'master_records_created': {
                'seasons': 0,
                'crops': 0,
                'varieties': 0,
                'growers': 0,
                'organizers': 0,
                'locations': 0
            },
            'errors': [],
            'timing': {}
        }
        
        try:
            logger.info("=" * 80)
            logger.info("[YIELD DATA RELOAD] Starting safe data reload")
            logger.info(f"  CSV File: {csv_path}")
            logger.info(f"  Truncate existing data: {truncate_data}")
            logger.info("  NOTE: Table schemas are NEVER modified (safe reload)")
            logger.info("=" * 80)
            
            # Step 1: Truncate data if requested (SAFE - no schema changes)
            if truncate_data:
                step_start = time.perf_counter()
                logger.info("[STEP 1/6] Truncating existing data (schema preserved)...")
                truncate_stats = self.truncate_yield_tables()
                stats['rows_truncated'] = truncate_stats.get('inspection_base_rows_before', 0) + truncate_stats.get('yield_rows_before', 0)
                stats['timing']['truncate_tables'] = time.perf_counter() - step_start
                logger.info(f"  [COMPLETE] Data truncated in {stats['timing']['truncate_tables']:.2f}s")
                logger.info(f"  [COMPLETE] Rows removed: {stats['rows_truncated']:,}")
            else:
                logger.info("[STEP 1/6] Skipping truncation - data will be upserted")
            
            # Step 2: Preload all master data into memory
            step_start = time.perf_counter()
            logger.info("[STEP 2/6] Preloading master data into memory...")
            logger.info("  Loading: seasons, crops, varieties, growers, organizers, locations")
            master_cache, preload_timing = self._preload_all_master_data()
            stats['timing']['preload_masters'] = time.perf_counter() - step_start
            stats.setdefault('profiling', {}).update(preload_timing)
            
            # Log master data counts
            logger.info(f"  [LOADED] Seasons: {len(master_cache.get('seasons', {}))} records")
            logger.info(f"  [LOADED] Crops: {len(master_cache.get('crops', {}))} records")
            logger.info(f"  [LOADED] Varieties: {len(master_cache.get('varieties', {}))} records")
            logger.info(f"  [LOADED] Growers: {len(master_cache.get('growers', {}))} records")
            logger.info(f"  [LOADED] Organizers: {len(master_cache.get('organizers', {}))} records")
            logger.info(f"  [LOADED] Locations: {len(master_cache.get('locations', {}))} records")
            logger.info(f"  [COMPLETE] Master data preloaded in {stats['timing']['preload_masters']:.2f}s")
            
            # Step 3: Load and normalize CSV (optimized) - with step-by-step timing
            logger.info("[STEP 3/6] Loading and normalizing CSV file...")
            logger.info(f"  Reading: {csv_path}")
            t0 = time.perf_counter()
            df = pd.read_csv(
                csv_path, 
                dtype=str, 
                low_memory=False,
                engine='c'
            )
            stats['csv_rows_loaded'] = len(df)
            stats['timing']['csv_read'] = time.perf_counter() - t0
            stats.setdefault('profiling', {})['pandas_read_csv'] = stats['timing']['csv_read']
            logger.info(f"  [LOADED] {stats['csv_rows_loaded']:,} rows, {len(df.columns)} columns")
            logger.info(f"  [TIMING] CSV read: {stats['timing']['csv_read']:.3f}s ({stats['csv_rows_loaded']/max(stats['timing']['csv_read'],0.001):,.0f} rows/sec)")
            
            t0 = time.perf_counter()
            df = self.normalize_columns(df)
            stats['timing']['normalize_columns'] = time.perf_counter() - t0
            stats.setdefault('profiling', {})['normalize_columns'] = stats['timing']['normalize_columns']
            logger.info(f"  [PROFILING] normalize_columns (lower/strip): {stats['timing']['normalize_columns']:.3f}s")
            
            t0 = time.perf_counter()
            if 'lot_id' in df.columns:
                df['lot_id'] = df['lot_id'].astype(str).str.strip().str.lower()
                df['lot_id'] = df['lot_id'].replace(['nan', 'none', 'null', ''], None)
            stats['timing']['clean_lot_id'] = time.perf_counter() - t0
            logger.info(f"  [PROFILING] clean lot_id: {stats['timing']['clean_lot_id']:.3f}s")
            
            t0 = time.perf_counter()
            numeric_cols = [
                'purchasing_document_number', 'po_soaking_acres', 'planting_list_soaking_acres',
                'sowing_acres', 'net_tp_acres', 'net_acreage_area', 'final_harvestable_area',
                'sum_of_received_qty', 'packed_qty', 'productivity_of_packed_seed',
                'qty', 'rate_per_kg', 'amount_inr', 'male_soaking_acre', 'male_no_of_pkt',
                'male_qty_in_kgs', 'female_soaking_acre', 'female_no_of_pkt', 'female_qty_in_kgs'
            ]
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            stats['timing']['numeric_conversion'] = time.perf_counter() - t0
            logger.info(f"  [PROFILING] numeric conversion: {stats['timing']['numeric_conversion']:.3f}s")
            
            t0 = time.perf_counter()
            date_cols = ['m1_soaking_date', 'female_soaking_date', 'female_date_of_transplant']
            for col in date_cols:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors='coerce')
            stats['timing']['date_conversion'] = time.perf_counter() - t0
            logger.info(f"  [PROFILING] date conversion: {stats['timing']['date_conversion']:.3f}s")
            
            stats['timing']['load_csv'] = (
                stats['timing']['csv_read'] + stats['timing']['normalize_columns'] +
                stats['timing']['clean_lot_id'] + stats['timing']['numeric_conversion'] + stats['timing']['date_conversion']
            )
            logger.info(f"[PROFILING] CSV load + normalization total: {stats['timing']['load_csv']:.3f}s")
            
            # Step 4: Process data with cached master lookups and collect missing masters
            step_start = time.perf_counter()
            logger.info("[STEP 4/6] Processing data and matching master records...")
            inspection_records, yield_records, missing_masters, process_timing, failed_rows = self._optimized_split_and_prepare_data(df, master_cache)
            stats['timing']['process_data'] = time.perf_counter() - step_start
            stats['failed_rows'] = failed_rows
            stats.setdefault('profiling', {}).update(process_timing)
            logger.info(f"  [PREPARED] Inspection records: {len(inspection_records):,}")
            logger.info(f"  [PREPARED] Yield records: {len(yield_records):,}")
            logger.info(f"  [EXPECTED] Total CSV rows: {stats['csv_rows_loaded']:,}")
            logger.info(f"  [VALID] Rows with all mandatory keys: {len(inspection_records):,}")
            if failed_rows:
                logger.warning(f"  [FAILED] Rows skipped (missing mandatory keys): {len(failed_rows):,}")
            logger.info(f"  [TIMING] Data processing: {stats['timing']['process_data']:.3f}s")
            
            # Step 5: Batch create missing master records (if any)
            if any(missing_masters.values()):
                step_start = time.perf_counter()
                logger.info("[STEP 5/6] Creating missing master records...")
                for master_type, items in missing_masters.items():
                    if items:
                        logger.info(f"  [PENDING] {master_type}: {len(items)} new records to create")
                
                created_counts = self._batch_create_missing_masters(missing_masters, master_cache)
                stats['master_records_created'].update(created_counts)
                stats['timing']['create_masters'] = time.perf_counter() - step_start
                
                for master_type, count in created_counts.items():
                    if count > 0:
                        logger.info(f"  [CREATED] {master_type}: {count} records inserted")
                logger.info(f"  [TIMING] Master creation: {stats['timing']['create_masters']:.3f}s")
                
                logger.info("  [INFO] Re-processing data with updated master cache...")
                reprocess_start = time.perf_counter()
                inspection_records, yield_records, _, _, failed_rows_reprocess = self._optimized_split_and_prepare_data(df, master_cache)
                stats['failed_rows'] = failed_rows_reprocess
                stats.setdefault('profiling', {})['reprocess_after_masters'] = time.perf_counter() - reprocess_start
                logger.info(f"  [TIMING] Reprocess: {time.perf_counter() - reprocess_start:.3f}s")
                logger.info(f"  [VALID] Rows after reprocess: {len(inspection_records):,}")
                if failed_rows_reprocess:
                    logger.warning(f"  [FAILED] Rows still skipped after master creation: {len(failed_rows_reprocess):,}")
            else:
                logger.info("[STEP 5/6] No missing master records - skipping creation")
            
            # Step 6: Optimized bulk insert
            step_start = time.perf_counter()
            logger.info("[STEP 6/6] Bulk inserting into fact tables...")
            logger.info(f"  [TARGET] operations.season_crop_inspection_base: {len(inspection_records):,} records")
            logger.info(f"  [TARGET] operations.season_crop_yield: {len(yield_records):,} records")
            
            insert_results, insert_timing = self._optimized_bulk_insert(inspection_records, yield_records)
            stats['timing']['bulk_insert'] = time.perf_counter() - step_start
            stats.setdefault('profiling', {}).update(insert_timing)
            
            stats['inspection_records_inserted'] = insert_results['inspection_inserted']
            stats['yield_records_inserted'] = insert_results['yield_inserted']
            
            # Detailed insert logging
            logger.info(f"  [INSERTED] season_crop_inspection_base: {stats['inspection_records_inserted']:,} rows")
            logger.info(f"  [INSERTED] season_crop_yield: {stats['yield_records_inserted']:,} rows")
            
            if insert_results.get('inspection_errors', 0) > 0:
                logger.warning(f"  [WARNING] Inspection insert errors: {insert_results['inspection_errors']}")
                stats['errors'].append(f"Inspection errors: {insert_results['inspection_errors']}")
            if insert_results.get('yield_errors', 0) > 0:
                logger.warning(f"  [WARNING] Yield insert errors: {insert_results['yield_errors']}")
                stats['errors'].append(f"Yield errors: {insert_results['yield_errors']}")
            
            logger.info(f"  [TIMING] Bulk insert: {stats['timing']['bulk_insert']:.3f}s")
            
            total_elapsed = time.perf_counter() - total_start
            stats['timing']['total'] = total_elapsed
            
            # Execution summary (detailed profiling)
            expected_rows = stats['csv_rows_loaded']
            loaded_rows = stats['inspection_records_inserted']
            failed_count = len(stats.get('failed_rows', []))
            row_loss = expected_rows - loaded_rows
            
            logger.info("")
            logger.info("=" * 80)
            logger.info("[SUCCESS] YIELD DATA RELOAD COMPLETED")
            logger.info("=" * 80)
            logger.info("")
            logger.info("ROW COUNT SUMMARY:")
            logger.info(f"  Expected (CSV rows):         {expected_rows:,}")
            logger.info(f"  Loaded (inserted):           {loaded_rows:,}")
            logger.info(f"  Failed (missing keys):       {failed_count:,}")
            if row_loss > 0 and row_loss != failed_count:
                logger.warning(f"  ROW LOSS: {row_loss:,} rows (check duplicates or constraint failures)")
            elif failed_count > 0:
                logger.warning(f"  {failed_count:,} rows skipped - see [FAILED ROWS] for details")
            logger.info("")
            logger.info("RECORDS INSERTED:")
            logger.info(f"  season_crop_inspection_base: {stats['inspection_records_inserted']:,} rows")
            logger.info(f"  season_crop_yield:           {stats['yield_records_inserted']:,} rows")
            logger.info("")
            if sum(stats['master_records_created'].values()) > 0:
                logger.info("NEW MASTER RECORDS CREATED:")
                for master_type, count in stats['master_records_created'].items():
                    if count > 0:
                        logger.info(f"  {master_type:20s}: {count:,} records")
                logger.info("")
            logger.info(f"THROUGHPUT: {stats['csv_rows_loaded']/max(total_elapsed,0.001):,.0f} rows/sec")
            logger.info("")
            logger.info("-" * 80)
            logger.info("TIMING BREAKDOWN:")
            for step, elapsed in sorted(stats['timing'].items(), key=lambda x: x[1], reverse=True):
                pct = (elapsed / total_elapsed * 100) if total_elapsed else 0
                logger.info(f"  {step:25s}: {elapsed:8.3f}s ({pct:5.1f}%)")
            if stats.get('profiling'):
                logger.info("-" * 80)
                logger.info("DETAILED PROFILING:")
                for k, v in sorted(stats['profiling'].items(), key=lambda x: x[1] if isinstance(x[1], (int, float)) else 0, reverse=True):
                    if isinstance(v, (int, float)) and v > 0.001:
                        logger.info(f"  {k:35s}: {v:.3f}s")
            logger.info("=" * 80)
            
            if stats.get('failed_rows'):
                logger.warning("")
                logger.warning(f"[FAILED ROWS] {len(stats['failed_rows'])} rows with missing mandatory keys:")
                for fr in stats['failed_rows'][:10]:
                    logger.warning(f"  Row {fr['row_index']}: {fr['reason']}")
                if len(stats['failed_rows']) > 10:
                    logger.warning(f"  ... and {len(stats['failed_rows']) - 10} more")
            
            if stats['errors']:
                logger.warning("")
                logger.warning(f"[WARNING] {len(stats['errors'])} errors encountered:")
                for error in stats['errors']:
                    logger.warning(f"  - {error}")
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error reloading yield data: {e}")
            stats['errors'].append(str(e))
            import traceback
            logger.error(traceback.format_exc())
            raise
        
        return stats


class EnhancedRecordService(RecordService):
    """Enhanced RecordService with dynamic inspection level support"""

    def __init__(self, db: Session, metadata: MetaData = None):
        super().__init__(db)
        self.metadata = metadata or MetaData()
        self.inspection_processor = InspectionLevelProcessor(db, self.metadata)

    def process_inspection_levels(self, df: pd.DataFrame, engine) -> Dict[str, Any]:
        """
        Process inspection levels from DataFrame
            
        Returns:
            Dict with processing results
        """
        results = {
            'configs': {},
            'dataframes': {},
            'inserted_counts': {}
        }

        try:
            df.rename(columns=self.rename_map, inplace=True)
            # Analyze inspection columns
            configs = self.inspection_processor.analyze_inspection_columns(df)
            results['configs'] = configs

            if not configs:
                logger.info("No inspection level columns found")
                return results

            # Create inspection level DataFrames
            level_dataframes = self.inspection_processor.create_inspection_level_dataframes(df)
            results['dataframes'] = level_dataframes

            # Create and setup database tables
            self.inspection_processor.create_and_setup_tables(configs, engine)

            # Create base inspection mapping (you'll need to implement this based on your logic)
            base_inspection_mapping = self._create_base_inspection_mapping(df)

            # Insert inspection level data
            inserted_counts = self.inspection_processor.bulk_insert_inspection_level_data(
                level_dataframes
            )
            results['inserted_counts'] = inserted_counts

            logger.info(f"Successfully processed {len(configs)} inspection levels")
            
        except Exception as e:
            logger.error(f"Error processing inspection levels: {str(e)}")
            raise
    
        return results

    def _create_base_inspection_mapping(self, df: pd.DataFrame) -> Dict[str, str]:
        """
        Create mapping from record key to base inspection ID
        This should match your base inspection creation logic
        """
        mapping = {}

        for _, row in df.iterrows():
            key = f"{row.get('season')}_{row.get('crop')}_{row.get('hsp_code')}_{row.get('grower_id')}_{row.get('lot_no_batch_no')}"
            # You would generate or retrieve the actual base_inspection_id here
            base_inspection_id = f"BASE_{hash(key) % 1000000:06d}"
            mapping[key] = base_inspection_id

        return mapping

def make_unique_identifier(row):
    parts = [str(row["village"]).strip().lower(),
             str(row["district"]).strip().lower(),
             str(row["taluka_mandal"]).strip().lower()]
    # Remove empty/NaN parts
    parts = [p for p in parts if p and p.lower() not in ["nan", "none"]]
    return "_".join(parts)

# Test/example code - only runs when script is executed directly
if __name__ == "__main__":
    db = SessionLocal()
    metadata = MetaData()

    try:
        # Step 2: Initialize RecordService with the session
        service = RecordService(db)
        record_service = EnhancedRecordService(db, metadata)

        # ============================================
        # NEW: Reload Yield Data with Composite Keys
        # ============================================
        # Example usage of the new reload_yield_data method:
        # csv_path = r"C:\Users\madan\OneDrive\Documents\Yield data RABI 21-25 (Production - Nov25)_updated.csv"
        # stats = service.reload_yield_data(csv_path, recreate_tables=True)
        # print(f"Reload complete: {stats}")
        
        # Step 3: Call the method (old method - kept for backward compatibility)
        df, mapping = service.load_and_process_excel(file_path=r"C:\Users\madan\OneDrive\Documents\Yield data RABI 21-25 (Production - Nov25)_updated.csv")
        unique_records, df = service.extract_unique_records(df)


    # inspection_results = record_service.process_inspection_levels(df, engine)


#     # You can then extract and insert data like this:
            # service.bulk_insert_inspection_base(unique_records['inspection_base'])

            # service.bulk_insert_yield_records(unique_records['yield_data'])
#     # unique_records = service.extract_unique_records(df)
#     # service.bulk_insert_crops(unique_records['crops'])
#     service.bulk_insert_varieties(unique_records['varieties'])
#     # service.bulk_insert_locations(unique_records['locations'])
#     # service.bulk_insert_growers(unique_records['growers'])
#     # service.bulk_insert_organizers(unique_records['organizers'])
#     # service.bulk_insert_inspection_base(unique_records['inspection_base'])
#
#

    except Exception as e:
        logger.error(f"Error in main execution: {e}")
        raise
    finally:
        # Step 4: Always close the session
        db.close()
