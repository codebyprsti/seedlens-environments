import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Tuple, Optional, Any, Set
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, create_engine, MetaData, Table
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import re
from datetime import datetime
from dataclasses import dataclass
from pydantic import BaseModel, create_model
from sqlalchemy.dialects.postgresql import UUID
import uuid

logger = logging.getLogger(__name__)

Base = declarative_base()


@dataclass
class InspectionLevelConfig:
    """Configuration for inspection level columns"""
    level: int
    columns: List[str]
    column_types: Dict[str, str]  # column_name -> data_type
    required_columns: List[str]


class DynamicInspectionLevelModel:
    """Factory for creating dynamic inspection level models"""

    def __init__(self, metadata: MetaData):
        self.metadata = metadata
        self.schema = "operations"
        self.created_models = {}
        self.created_tables = {}
        self.rename_map = {
            'crop': 'crop_name',
            'hsp_code': 'variety_name',
            'village_id': 'location_id',
            'taluka_mandal': 'mandal',
            'father_s_name': 'fathers_name',
            'org_id': 'organizer_id',
            'yield_production_plant': 'production_plant'
        }

    def create_inspection_level_table(self, level: int, columns_config: Dict[str, str]) -> Table:
        """
        Create a dynamic table for inspection level

        Args:
            level: Inspection level number
            columns_config: Dict of column_name -> data_type
        """
        table_name = f"inspection_level_{level}"

        if table_name in self.created_tables:
            return self.created_tables[table_name]

        # Base columns that every inspection level should have
        # CRITICAL: lot_no, season, and crop must be included as base columns
        base_columns = [
            Column('id', String, primary_key=True),
            Column('inspection_level', Integer, default=level),
            Column('inspection_date', DateTime),
            Column('lot_no', String),  # Required for comparisons - must be normalized
            Column('season', String),  # Required - populated from Excel
            Column('crop', String),  # Required - populated from Excel
            Column('created_at', DateTime, default=datetime.utcnow),
            Column('updated_at', DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
        ]

        # Dynamic columns based on the configuration
        dynamic_columns = []
        for col_name, data_type in columns_config.items():
            if col_name not in ['id', 'base_inspection_id', 'inspection_level', 'inspection_date', 'inspector_name']:
                # Sanitize column name for SQL compatibility and normalize to lowercase
                import re
                sanitized_col_name = str(col_name).replace('/', '_').replace(' ', '_').replace('-', '_')
                sanitized_col_name = sanitized_col_name.replace('(', '').replace(')', '').replace('.', '_')
                sanitized_col_name = re.sub(r'_+', '_', sanitized_col_name).strip('_')
                sanitized_col_name = sanitized_col_name.lower()  # Normalize to lowercase for consistency
                
                if data_type.lower() in ['string', 'str', 'text']:
                    dynamic_columns.append(Column(sanitized_col_name, String))
                elif data_type.lower() in ['integer', 'int']:
                    dynamic_columns.append(Column(sanitized_col_name, Integer))
                elif data_type.lower() in ['float', 'decimal', 'number']:
                    dynamic_columns.append(Column(sanitized_col_name, Float))
                elif data_type.lower() in ['datetime', 'date']:
                    dynamic_columns.append(Column(sanitized_col_name, DateTime))
                elif data_type.lower() in ['text', 'longtext']:
                    dynamic_columns.append(Column(sanitized_col_name, Text))
                else:
                    # Default to String for unknown types
                    dynamic_columns.append(Column(sanitized_col_name, String))

        # Create the table
        table = Table(
            table_name,
            self.metadata,
            *base_columns,
            *dynamic_columns,
            extend_existing=True,
            schema="operations"
        )

        self.created_tables[table_name] = table
        return table

    def create_pydantic_model(self, level: int, columns_config: Dict[str, str]) -> BaseModel:
        """Create a dynamic Pydantic model for validation"""
        model_name = f"InspectionLevel{level}Create"

        if model_name in self.created_models:
            return self.created_models[model_name]

        # Base fields
        fields = {
            'base_inspection_id': (str, ...),
            'inspection_level': (int, level),
            'inspection_date': (Optional[datetime], None),
            'inspector_name': (Optional[str], None),
        }

        # Add dynamic fields
        for col_name, data_type in columns_config.items():
            if col_name not in fields:
                if data_type.lower() in ['string', 'str', 'text']:
                    fields[col_name] = (Optional[str], None)
                elif data_type.lower() in ['integer', 'int']:
                    fields[col_name] = (Optional[int], None)
                elif data_type.lower() in ['float', 'decimal', 'number']:
                    fields[col_name] = (Optional[float], None)
                elif data_type.lower() in ['datetime', 'date']:
                    fields[col_name] = (Optional[datetime], None)
                else:
                    fields[col_name] = (Optional[str], None)

        # Create the model
        model = create_model(model_name, **fields, __base__=BaseModel)
        self.created_models[model_name] = model
        return model

class InspectionLevelProcessor:

    """Processor for handling inspection level data"""

    def __init__(self, db: Session, metadata: MetaData):
        self.db = db
        self.metadata = metadata
        self.dynamic_model_factory = DynamicInspectionLevelModel(metadata)
        self.inspection_configs = {}

    def analyze_inspection_columns(self, df: pd.DataFrame) -> Dict[int, InspectionLevelConfig]:
        """
        Analyze DataFrame to identify inspection level columns and their types.

        Returns:
            Dict mapping inspection level to configuration
        """
        inspection_columns = {}

        # Pattern to match inspection columns like inspection_1_date
        inspection_pattern = re.compile(r'inspection_(\d+)_(.+)', re.IGNORECASE)

        for column in df.columns:
            match = inspection_pattern.match(str(column))
            if match:
                level = int(match.group(1))
                field_name = match.group(2)

                if level not in inspection_columns:
                    inspection_columns[level] = []

                inspection_columns[level].append({
                    'original_column': column,
                    'field_name': field_name,
                    'data_type': self._infer_column_type(df[column])
                })

        # Add static columns like season, crop_name etc. to each inspection level
        identifier_columns = ['season_id', 'crop_id', 'variety_id', 'grower_id', 'lot_no']  # Changed lot_id to lot_no
        for level in inspection_columns:
            for col in identifier_columns:
                if col in df.columns:
                    inspection_columns[level].append({
                        'original_column': col,
                        'field_name': col,
                        'data_type': self._infer_column_type(df[col])
                    })

        # Create configurations
        configs = {}
        for level, columns in inspection_columns.items():
            column_types = {}
            column_names = []

            for col_info in columns:
                column_types[col_info['field_name']] = col_info['data_type']
                column_names.append(col_info['field_name'])

            configs[level] = InspectionLevelConfig(
                level=level,
                columns=column_names,
                column_types=column_types,
                required_columns=['production'] if 'production' in column_names else []
            )

        return configs

    def _infer_column_type(self, series: pd.Series) -> str:
        """Infer the data type of a pandas Series"""
        # Remove null values for type inference
        non_null_series = series.dropna()

        if len(non_null_series) == 0:
            return 'string'

        # Check if it's numeric
        if pd.api.types.is_numeric_dtype(non_null_series):
            if pd.api.types.is_integer_dtype(non_null_series):
                return 'integer'
            else:
                return 'float'

        # Check if it's datetime
        if pd.api.types.is_datetime64_any_dtype(non_null_series):
            return 'datetime'

        # Check if it looks like a date string
        if non_null_series.dtype == 'object':
            sample_values = non_null_series.head(10).astype(str)
            date_patterns = [
                r'\d{4}-\d{2}-\d{2}',
                r'\d{2}/\d{2}/\d{4}',
                r'\d{2}-\d{2}-\d{4}'
            ]

            for pattern in date_patterns:
                if any(re.match(pattern, str(val)) for val in sample_values):
                    return 'datetime'

        # Default to string
        return 'string'

    def create_inspection_level_dataframes(self, df: pd.DataFrame) -> Dict[int, pd.DataFrame]:
        """
        Create separate DataFrames for each inspection level

        Returns:
            Dict mapping inspection level to DataFrame
        """
        configs = self.analyze_inspection_columns(df)
        level_dataframes = {}

        for level, config in configs.items():
            # Get base columns (non-inspection specific)
            base_columns = [col for col in df.columns if not re.match(r'inspection_\d+_|yield', str(col), re.IGNORECASE)]

            # Get inspection level specific columns
            inspection_columns = [col for col in df.columns if
                                  re.match(f'inspection_{level}_', str(col), re.IGNORECASE)]

            # Combine columns
            level_columns = base_columns + inspection_columns

            # Create DataFrame for this level
            level_df = df[level_columns].copy()

            # Rename inspection columns to remove the level prefix
            rename_dict = {}
            for col in inspection_columns:
                new_name = re.sub(f'inspection_{level}_', '', str(col), flags=re.IGNORECASE)
                rename_dict[col] = new_name

            level_df = level_df.rename(columns=rename_dict)

            # Add metadata columns
            level_df['inspection_level'] = level
            level_df['id'] = [f"IL{level}_{i:06d}" for i in range(len(level_df))]
            for col in level_df.columns:
                if 'date' in col.lower():
                    level_df[col] = pd.to_datetime(level_df[col], dayfirst=True, errors='coerce')

            # Filter out rows where all inspection-specific columns are null
            inspection_field_names = [rename_dict[col] for col in inspection_columns]
            level_df = level_df.dropna(subset=inspection_field_names, how='all')

            level_dataframes[level] = level_df

            logger.info(f"Created DataFrame for inspection level {level} with {len(level_df)} rows")

        return level_dataframes

    def create_and_setup_tables(self, configs: Dict[int, InspectionLevelConfig], engine):
        """Create database tables for each inspection level"""
        for level, config in configs.items():
            # Create table
            table = self.dynamic_model_factory.create_inspection_level_table(
                level=level,
                columns_config=config.column_types
            )

            # Create Pydantic model
            model = self.dynamic_model_factory.create_pydantic_model(
                level=level,
                columns_config=config.column_types
            )

            logger.info(f"Created table and model for inspection level {level}")

        self.metadata.reflect(bind=engine, schema="operations")

        # Create all tables
        self.metadata.create_all(engine)

    def sanitize_record(self, record: dict) -> dict:
        """
        Ensure all values in the record are SQLAlchemy-compatible with proper NULL handling
        """
        sanitized = {}
        for k, v in record.items():
            if v is None or pd.isna(v):  # Explicit None check + pandas NA
                sanitized[k] = None
            elif isinstance(v, (np.integer, np.int64)):
                sanitized[k] = int(v)
            elif isinstance(v, (np.floating, np.float64)):
                sanitized[k] = float(v)
            elif isinstance(v, np.bool_):
                sanitized[k] = bool(v)
            elif isinstance(v, pd.Timestamp):
                sanitized[k] = v.to_pydatetime()
            elif isinstance(v, (list, dict, np.ndarray)):
                sanitized[k] = str(v)  # Serialize complex objects
            else:
                sanitized[k] = v
        return sanitized

    def bulk_insert_inspection_level_data(self, level_dataframes: Dict[int, pd.DataFrame]) -> Dict[int, int]:
        """
        Simplified version without base_inspection mapping
        Inserts all rows without filtering or deduplication
        """
        inserted_counts = {}

        for level, level_df in level_dataframes.items():
            try:
                table_name = f"inspection_level_{level}"
                table = self.dynamic_model_factory.created_tables.get(table_name)

                if table is None or not isinstance(table, Table):
                    logger.warning(f"Table not found for level {level}")
                    inserted_counts[level] = 0
                    continue

                # Get valid table columns
                valid_columns = set(table.columns.keys())
                logger.info(f"Table {table_name} has {len(valid_columns)} columns")
                
                # Remove any reference to base_inspection_id in the DataFrame
                if 'base_inspection_id' in level_df.columns:
                    level_df = level_df.drop(columns=['base_inspection_id'])

                # Prepare records - filter to only include valid table columns
                records = []
                skipped_cols = set()
                for idx, row in level_df.iterrows():
                    record = row.to_dict()
                    # Sanitize the record first
                    sanitized_record = self.sanitize_record(record)
                    # Filter to only include columns that exist in the table
                    filtered_record = {}
                    for key, value in sanitized_record.items():
                        if key in valid_columns:
                            filtered_record[key] = value
                        else:
                            if idx == 0:  # Only log once for first record
                                skipped_cols.add(key)
                    
                    # Ensure all required columns are present (set to None if missing)
                    for col in valid_columns:
                        if col not in filtered_record:
                            filtered_record[col] = None
                    
                    records.append(filtered_record)
                
                if skipped_cols and len(records) > 0:
                    logger.info(f"Skipped {len(skipped_cols)} columns not in table: {list(skipped_cols)[:10]}")

                if records:
                    logger.info(f"Prepared {len(records)} records for insertion into {table_name}")
                    if len(records) > 0:
                        logger.info(f"Sample record keys (first record): {list(records[0].keys())[:10]}")
                        logger.info(f"Table columns count: {len(valid_columns)}")
                    
                    # Use engine connection directly to avoid session/boolean evaluation issues
                    engine = getattr(self, 'engine', None)
                    if engine is None:
                        # Try to get from db bind
                        engine = self.db.bind if hasattr(self.db, 'bind') else None
                    
                    if engine is None:
                        # Fallback: use session execute - insert records one by one to avoid boolean evaluation
                        logger.warning("No engine available, using session execute row-by-row")
                        total_inserted = 0
                        for idx, record in enumerate(records):
                            try:
                                ins = table.insert()
                                self.db.execute(ins, [record])
                                if (idx + 1) % 1000 == 0:
                                    self.db.commit()
                                    logger.info(f"Committed {idx + 1} records...")
                                    total_inserted = idx + 1
                            except Exception as row_error:
                                import traceback
                                logger.error(f"Error inserting row {idx}: {row_error}")
                                logger.error(f"Row traceback: {traceback.format_exc()}")
                                self.db.rollback()
                                raise
                        # Final commit
                        self.db.commit()
                        inserted_counts[level] = len(records)
                        logger.info(f"Inserted all {len(records)} records row-by-row")
                    else:
                        # Use engine connection directly - this avoids boolean evaluation issues
                        with engine.begin() as conn:
                            batch_size = 1000
                            total_inserted = 0
                            for i in range(0, len(records), batch_size):
                                batch = records[i:i + batch_size]
                                try:
                                    ins = table.insert()
                                    conn.execute(ins, batch)
                                    total_inserted += len(batch)
                                    if (i // batch_size + 1) % 10 == 0 or i + batch_size >= len(records):
                                        logger.info(f"Inserted batch {i//batch_size + 1}: {len(batch)} records (total: {total_inserted}/{len(records)})")
                                except Exception as batch_error:
                                    import traceback
                                    logger.error(f"Error inserting batch {i//batch_size + 1}: {batch_error}")
                                    logger.error(f"Batch traceback: {traceback.format_exc()}")
                                    raise
                            inserted_counts[level] = total_inserted
                            # Commit session as well for consistency
                            try:
                                self.db.commit()
                            except:
                                pass
                    
                    logger.info(f"Successfully inserted {inserted_counts[level]} records for level {level}")

            except Exception as e:
                logger.error(f"Error inserting level {level} data: {str(e)}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                self.db.rollback()
                inserted_counts[level] = 0

        return inserted_counts


    def _create_base_inspection_key(self, record: Dict) -> str:
        """Create a key to match with base inspection records"""
        # This should match the key creation logic used in your base inspection creation
        return f"{record.get('season_id')}_{record.get('crop_id')}_{record.get('variety_id')}_{record.get('grower_id')}_{record.get('lot_id')}"

    def _clean_inspection_record(self, record: Dict[str, Any], level: int) -> Dict[str, Any]:
        table_name = f"inspection_level_{level}"
        table = self.dynamic_model_factory.created_tables.get(table_name)

        if table is None:
            return {}

        valid_columns = table.columns.keys()
        
        # CRITICAL: Never drop season and crop columns, even if NULL
        # These are required columns and must be present in every record
        protected_columns = {'season', 'crop', 'lot_no'}

        # Keep only the keys that match table columns
        # For protected columns (season, crop, lot_no), include even if NULL
        # For other columns, drop nulls
        cleaned = {}
        for key, value in record.items():
            if key in valid_columns:
                key_lower = key.lower()
                # Always include protected columns, even if NULL
                if key_lower in protected_columns:
                    cleaned[key] = value  # Include even if NULL
                elif pd.notnull(value):
                    cleaned[key] = value  # Only include non-null for other columns
        
        # CRITICAL: Ensure season and crop are always present (set to None if missing)
        for protected_col in protected_columns:
            if protected_col not in cleaned:
                cleaned[protected_col] = None

        return cleaned
