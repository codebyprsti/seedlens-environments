import pandas as pd
import logging
from typing import Dict, List, Tuple, Optional, Any

from sqlalchemy import MetaData
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from models.db_models import (
    CropRecord, VarietyRecord, LocationRecord,
    GrowerRecord, OrganizerRecord, SeasonCropInspectionBase
)
from services.inspection_service import InspectionLevelProcessor
from models.schemas.base import (
    CropRecordCreate,
    VarietyRecordCreate,
    LocationRecordCreate,
    GrowerRecordCreate,
    OrganizerRecordCreate,
    SeasonCropInspectionBaseCreate
)
from core.db import SessionLocal, engine
# from services.record_service import RecordService

from core.config import settings
import re
from datetime import datetime

logger = logging.getLogger(__name__)


class RecordService:
    def __init__(self, db: Session):
        self.db = db
        self.rename_map = {
        'crop': 'crop_name',
        'hsp_code': 'variety_name',
        'village_id': 'location_id',
        'taluka_mandal': 'mandal',
        'taluka_id': 'mandal_id',
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

    def read_csv_preserve_ids(self, file_path: str):
        # Read only headers (first 2 rows, since it's multi-level)
        headers = pd.read_csv(file_path, nrows=0, header=[0, 1]).columns

        # Prepare dtype mapping: columns ending with 'id' => str
        dtype = {
            col: str for col in headers if col[1].strip().lower().endswith("id")
        }

        # Read full CSV with dtype applied only to 'id' columns
        df = pd.read_csv(file_path, header=[0, 1], dtype=dtype)

        return df

    def load_and_process_excel(self, file_path: str) -> Tuple[pd.DataFrame, Dict[str, str]]:
        """
        Load Excel file with two-level headers and flatten them.
        Returns processed DataFrame and header mapping.
        """
        try:
            # Read the Excel file with two header rows
            df = self.read_csv_preserve_ids(file_path)
            # Extract column tuples
            cols = list(df.columns)

            # Step 1: Strip and normalize both levels
            level_0 = [str(c[0]).strip() if c[0] else '' for c in cols]
            level_1 = [str(c[1]).strip() if c[1] else '' for c in cols]

            # Step 2: Replace 'Unnamed' with None and ffill level 0
            cleaned_level_0 = []
            last = None
            for col in level_0:
                if re.search(r"(?i)unnamed", col) or col == '' or pd.isna(col):
                    cleaned_level_0.append(last)
                else:
                    last = col
                    cleaned_level_0.append(col)

            # Step 3: Combine into MultiIndex
            df.columns = pd.MultiIndex.from_tuples(zip(cleaned_level_0, level_1))

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

    def clean_null_values(self,record: dict) -> dict:
        for key, value in record.items():
            if isinstance(value, float) and pd.isna(value):
                record[key] = None
            elif value == 'nan':
                record[key] = None
        return record

    def bulk_insert_inspection_base(self, base_records: List[Dict]) -> int:
        """
        Bulk insert SeasonCropInspectionBase records with duplicate handling.
        """
        try:
            if not base_records:
                return 0

            # Get existing combinations (avoid full duplication check on all columns, so we check key uniqueness)
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

    def extract_unique_records(self, df: pd.DataFrame) -> Dict[str, List[Dict]]:
        """
        Extract unique records for foundation tables + base + inspection from the DataFrame.
        """
        unique_records = {
            'crops': [],
            'varieties': [],
            'locations': [],
            'growers': [],
            'organizers': [],
            'inspection_base': [],
            'inspection_final': []
        }

        try:
            # Extract unique crops
            crop_columns = self._find_columns(df, ['crop'])
            if crop_columns:
                crops_df = df[crop_columns].drop_duplicates().dropna()
                crop_records = crops_df.to_dict('records')
                normalized = [self.normalize_keys(r) for r in crop_records]

                # Generate crop_id: CR_001, CR_002, ...
                crops_with_ids = self._generate_sequential_ids(normalized, prefix="CR_", pad=3, id_key="crop_id")
                unique_records['crops'] = crops_with_ids
                # Create a mapping from crop_name to crop_id
                crop_name_to_id = {
                    crop['crop_name'].strip().lower(): crop['crop_id']
                    for crop in crops_with_ids
                }

            # Extract unique varieties
            variety_columns = self._find_columns(df, [
                'hsp_code', "crop",
                'male_parent_seed_lot_no', 'female_parent_seed_lot_no'
            ])
            if variety_columns:
                varieties_df = df[variety_columns].drop_duplicates().dropna(subset=['hsp_code'])
                records = varieties_df.to_dict('records')
                normalized_varieties = [self.normalize_keys(r) for r in records]
                # Replace crop_name with corresponding crop_id
                for variety in normalized_varieties:
                    crop_name = variety.get('crop_name', '').strip().lower()
                    crop_id = crop_name_to_id.get(crop_name)
                    if crop_id:
                        variety['crop_id'] = crop_id
                    variety.pop('crop_name', None)  # r

                # Generate variety_id: VR_1001, VR_1002, ...
                varieties_with_ids = self._generate_sequential_ids(normalized_varieties, prefix="VR_", start=1001, pad=4,
                                                                   id_key="variety_id")
                unique_records['varieties'] = varieties_with_ids
                variety_name_to_id = {
                    v['variety_name'].strip().lower(): v['variety_id']
                    for v in varieties_with_ids
                }

            # Extract unique locations
            location_columns = self._find_columns(df, [
                'village_id', 'village', 'taluka_mandal', 'district', 'state', 'taluka_id', 'district_id'
            ])
            if location_columns:
                locations_df = df[location_columns].drop_duplicates().dropna(subset=['village_id'])
                locations_df["category_id"] = 100004
                records = locations_df.to_dict('records')
                unique_records['locations'] = [self.normalize_keys(r) for r in records]

            # Extract unique growers
            grower_columns = self._find_columns(df, [
                'grower_id', 'grower_name', 'father_s_name', 'grower_gender'
            ])
            if grower_columns:
                growers_df = df[grower_columns].drop_duplicates().dropna(subset=['grower_id'])
                growers_df["category_id"] = 100005
                records = growers_df.to_dict('records')
                unique_records['growers'] = [self.normalize_keys(r) for r in records]

            # Extract unique organizers
            organizer_columns = self._find_columns(df, ['org_id', 'organizer_name', 'yield_production_plant'])
            if organizer_columns:
                organizers_df = df[organizer_columns].drop_duplicates().dropna(subset=['org_id'])
                organizers_df["category_id"] = 100006
                records =  organizers_df.to_dict('records')
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
            unique_records['inspection_final'] = [self.normalize_keys(r) for r in records]            # Extract full inspection data


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
            new_records_df = new_records_df.drop_duplicates(subset='location_id')
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
            new_records_df = new_records_df.drop_duplicates(subset='grower_id')
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

    def bulk_insert_organizers(self, organizer_records: List[Dict]) -> int:
        """
        Bulk insert organizer records with duplicate handling.
        """
        try:
            if not organizer_records:
                return 0

            # Get existing organizer IDs
            existing_organizer_ids = set(
                organizer_id[0] for organizer_id in self.db.query(OrganizerRecord.organizer_id).all()
            )

            # Filter out existing records
            new_records = [
                record for record in organizer_records
                if record.get('organizer_id') not in existing_organizer_ids
            ]
            new_records_df = pd.DataFrame(new_records)
            new_records_df = new_records_df.drop_duplicates(subset='organizer_id')
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


db = SessionLocal()
metadata = MetaData()

try:
    # Step 2: Initialize RecordService with the session
    service = RecordService(db)
    record_service = EnhancedRecordService(db, metadata)

    # Step 3: Call the method
    df, mapping = service.load_and_process_excel(file_path="C:\\Users\\madan\\Downloads\\Rabi 24-25 formatted report latest.csv")
    unique_records, df = service.extract_unique_records(df)


    # inspection_results = record_service.process_inspection_levels(df, engine)


    # You can then extract and insert data like this:
    # unique_records = service.extract_unique_records(df)
    # service.bulk_insert_inspection_base(unique_records['inspection_base'])
    # service.bulk_insert_crops(unique_records['crops'])
    # service.bulk_insert_varieties(unique_records['varieties'])
    service.bulk_insert_locations(unique_records['locations'])
    # service.bulk_insert_growers(unique_records['growers'])
    # service.bulk_insert_organizers(unique_records['organizers'])
    # service.bulk_insert_inspection_base(unique_records['inspection_base'])


finally:
    # Step 4: Always close the session
    db.close()