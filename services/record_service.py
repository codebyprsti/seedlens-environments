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

from sqlalchemy import MetaData, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from models.db_models import (
    CropRecord, VarietyRecord, LocationRecord,
    GrowerRecord, OrganizerRecord, SeasonCropInspectionBase, YieldRecord, SupplyChainPlanning
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


class RecordService:
    def __init__(self, db: Session):
        self.db = db
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
        # Initialize caches - handle connection errors gracefully
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

    def bulk_insert_inspection_base(self, base_records: List[Dict]) -> int:
        """
        Bulk insert SeasonCropInspectionBase records with duplicate handling.
        """
        try:
            if not base_records:
                return 0

            # Get existing combinations (avoid full duplication check on all columns, so we check key uniqueness)
            # Handle database connection errors gracefully
            try:
                existing_keys = set(
                    (r.season_id, r.crop_id, r.variety_id, r.grower_id, r.lot_id)
                    for r in self.db.query(
                        SeasonCropInspectionBase.season_id,
                        SeasonCropInspectionBase.crop_id,
                        SeasonCropInspectionBase.variety_id,
                        SeasonCropInspectionBase.grower_id,
                        SeasonCropInspectionBase.lot_id
                    ).all()
                )
            except Exception as db_error:
                # Database not available - skip duplicate check, insert all records
                logger.warning(f"Could not check for existing records (database may not be available): {db_error}")
                logger.info("Proceeding without duplicate check - all records will be attempted for insertion")
                existing_keys = set()

            # Filter out existing records by (season_id, crop_id, variety_id, grower_id, lot_id)
            new_records = []
            for record in base_records:
                key = (
                    record.get('season_id'),
                    record.get('crop_id'),
                    record.get('variety_id'),
                    record.get('grower_id'),
                    record.get('lot_id')
                )
                if key not in existing_keys:
                    new_records.append(record)

            if not new_records:
                logger.info("No new inspection base records to insert")
                return 0

            # Validate and create records
            validated_records = []
            for record in new_records:
                try:
                    record = self.clean_null_values(record)
                    # if any(record.get(field) is None for field in self.REQUIRED_FIELDS):
                    #     logger.warning(f"Skipping record due to missing required fields: {record}")
                    #     continue
                    base_schema = SeasonCropInspectionBaseCreate(**record)
                    validated_records.append(SeasonCropInspectionBase(**base_schema.dict()))
                except Exception as e:
                    logger.warning(f"Invalid inspection_base record {record}: {str(e)}")
                    continue

            # Bulk insert
            if validated_records:
                self.db.bulk_save_objects(validated_records)
                self.db.commit()
                self.processed_counts['inspection_base'] = len(validated_records)
                logger.info(f"Inserted {len(validated_records)} new inspection base records")

            return len(validated_records)

        except Exception as e:
            logger.error(f"Error bulk inserting inspection base records: {str(e)}")
            self.db.rollback()
            raise

    def normalize_keys(self, record: dict) -> dict:
        return {
            self.rename_map.get(k.strip(), k.strip()): v
            for k, v in record.items()
        }

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
            logger.info(f"Created new crop: {new_id} - {crop_name}")
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
            logger.info(f"Created new variety: {new_id} - {variety_name} (crop_id: {crop_id})")
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
                self.bulk_insert_inspection_base(unique_records["season_crop_inspection_base"])
            except Exception as e:
                logger.warning(f"Could not insert inspection base records (database may not be available): {e}")
                logger.info("Continuing with data processing...")
            
            unique_records = self._process_yield_records(df, unique_records)
            # Try to insert, but don't fail if database isn't available
            try:
                self.bulk_insert_yield_records(unique_records["season_crop_yield"])
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

    def bulk_insert_yield_records(self, yield_records: List[Dict]) -> int:
        """
        Bulk insert yield records with deduplication and validation.
        """
        try:
            if not yield_records:
                return 0

            # Fetch existing composite keys to avoid duplicate inserts
            # Handle database connection errors gracefully
            try:
                existing_keys = set(
                    tuple(r) for r in self.db.query(
                        YieldRecord.grower_id,
                        YieldRecord.crop_id,
                        YieldRecord.lot_id,
                        YieldRecord.season_id,
                        YieldRecord.variety_id
                    ).all()
                )
            except Exception as db_error:
                # Database not available - skip duplicate check, insert all records
                logger.warning(f"Could not check for existing yield records (database may not be available): {db_error}")
                logger.info("Proceeding without duplicate check - all records will be attempted for insertion")
                existing_keys = set()

            # Deduplicate incoming records
            def record_key(rec: Dict):
                return (
                    rec.get("grower_id"),
                    rec.get("crop_id"),
                    rec.get("lot_id"),
                    rec.get("season_id"),
                    rec.get("variety_id")
                )

            new_records = [
                r for r in yield_records if record_key(r) not in existing_keys
            ]

            new_df = pd.DataFrame(new_records)
            # new_df = new_df.drop_duplicates(
            #     subset=["grower_id", "crop_id", "lot_id", "season_id", "variety_id"]
            # )
            new_records = new_df.to_dict(orient="records")

            if not new_records:
                logger.info("No new yield records to insert")
                return 0

            # Get valid column names from the YieldRecord model
            # This gets columns defined in the SQLAlchemy model
            model_columns = set(YieldRecord.__table__.columns.keys())
            
            # Query actual database table columns to see what exists
            # Use model columns as default, only filter if we can successfully inspect
            valid_columns = model_columns
            try:
                from sqlalchemy import inspect
                from sqlalchemy.exc import OperationalError
                # Get engine from the session
                engine = self.db.bind if hasattr(self.db, 'bind') else sync_engine
                inspector = inspect(engine)
                table_columns = inspector.get_columns('season_crop_yield', schema='operations')
                actual_db_columns = {col['name'] for col in table_columns}
                # Use intersection - only columns that exist in both model and database
                valid_columns = model_columns.intersection(actual_db_columns)
                logger.info(f"Database table has {len(actual_db_columns)} columns, model has {len(model_columns)} columns")
                if model_columns - actual_db_columns:
                    missing_cols = model_columns - actual_db_columns
                    logger.warning(f"Columns in model but not in database: {missing_cols}. These will be filtered out.")
            except (OperationalError, AttributeError, Exception) as e:
                # If we can't inspect (database not available, connection error, etc.), 
                # use model columns and let SQLAlchemy handle missing columns at insert time
                # This is expected if database is not running or not accessible
                logger.debug(f"Could not inspect database schema (database may not be available), using model columns only: {type(e).__name__}")
                valid_columns = model_columns

            # Validate and transform
            validated_yield_records = []
            for record in new_records:
                try:
                    record = self._clean_record_for_validation(record)
                    
                    # Filter out columns that don't exist in the database table
                    filtered_record = {k: v for k, v in record.items() if k in valid_columns}
                    
                    # Log if any columns were filtered out (only for first record to avoid spam)
                    if validated_yield_records == []:
                        removed_cols = set(record.keys()) - set(filtered_record.keys())
                        if removed_cols:
                            logger.info(f"Filtered out columns not in database table: {removed_cols}")
                    
                    string_fields = [
                        'production_co', 'purchase_order', 'slab', 'tp_days_slab',
                        'production_location', 'production_manager', 'pos_done_b'
                    ]

                    for field in string_fields:
                        if field in filtered_record and filtered_record[field] is not None:
                            filtered_record[field] = str(filtered_record[field])

                    validated = YieldRecordBase(**filtered_record)
                    validated_yield_records.append(YieldRecord(**validated.dict()))
                except Exception as e:
                    logger.warning(f"Invalid yield record skipped: {record} — Error: {str(e)}")
                    continue

            if validated_yield_records:
                self.db.bulk_save_objects(validated_yield_records)
                self.db.commit()
                self.processed_counts["yield"] = len(validated_yield_records)
                logger.info(f"Inserted {len(validated_yield_records)} yield records")

            return len(validated_yield_records)

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

        # FIXED: Location lookup now considers hierarchy (village, district, state)
        def get_location_id(row):
            village = str(row.get('village', '')).strip().lower() if row.get('village') else ''
            district = str(row.get('district', '')).strip().lower() if row.get('district') else ''
            state = str(row.get('state', '')).strip().lower() if row.get('state') else ''
            
            if not village:
                return None
        
            # Try most specific first: village + district + state
            if village and district and state:
                key_full = f"{village}|{district}|{state}"
                if key_full in self.location_name_to_id:
                    return self.location_name_to_id[key_full]
            
            # Try secondary: village + district
            if village and district:
                key_district = f"{village}|{district}"
                if key_district in self.location_name_to_id:
                    return self.location_name_to_id[key_district]
            
            # Fallback to village only (for backward compatibility)
            return self.location_name_to_id.get(village)
        
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

        # Normalize types
        df["lot_id"] = df["lot_id"].astype(str)

        # Updated required_columns to match your model exactly
        # FIXED: Added total_recieved_qty and productivity to required columns
        required_columns = [
            "grower_id", "crop_id", "lot_no", "season_id", "variety_id", "location_id",
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

        df = df[required_columns]
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

        # FIXED: Location lookup now considers hierarchy (village, district, state)
        def get_location_id(row):
            village = str(row.get('village', '')).strip().lower() if row.get('village') else ''
            district = str(row.get('district', '')).strip().lower() if row.get('district') else ''
            state = str(row.get('state', '')).strip().lower() if row.get('state') else ''
            
            if not village:
                return None
        
            # Try most specific first: village + district + state
            if village and district and state:
                key_full = f"{village}|{district}|{state}"
                if key_full in self.location_name_to_id:
                    return self.location_name_to_id[key_full]
            
            # Try secondary: village + district
            if village and district:
                key_district = f"{village}|{district}"
                if key_district in self.location_name_to_id:
                    return self.location_name_to_id[key_district]
            
            # Fallback to village only (for backward compatibility)
            return self.location_name_to_id.get(village)
        
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

        # Default any missing columns to empty string or None based on type
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

        df = df[all_required_cols]  # keep only required columns
        df = df.drop_duplicates()
        # Use lot_no instead of lot_id
        if 'lot_no' in df.columns:
            df["lot_no"] = df["lot_no"].astype(str)
        df["production_code"] = df["production_code"].astype(str)
        # Convert to dict records
        records = df.to_dict('records')
        unique_records['season_crop_inspection_base'] = [self.normalize_keys(r) for r in records]

        return unique_records




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

        # Step 3: Call the method
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
