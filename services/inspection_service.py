import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Tuple, Optional, Any, Set
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, create_engine, MetaData, Table
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, relationship
from sqlalchemy.exc import IntegrityError
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
        base_columns = [
            Column('id', String, primary_key=True),
            Column('inspection_level', Integer, default=level),
            Column('inspection_date', DateTime),
            Column('created_at', DateTime, default=datetime.utcnow),
            Column('updated_at', DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
        ]

        # Dynamic columns based on the configuration
        dynamic_columns = []
        for col_name, data_type in columns_config.items():
            if col_name not in ['id', 'base_inspection_id', 'inspection_level', 'inspection_date', 'inspector_name']:
                if data_type.lower() in ['string', 'str', 'text']:
                    dynamic_columns.append(Column(col_name, String))
                elif data_type.lower() in ['integer', 'int']:
                    dynamic_columns.append(Column(col_name, Integer))
                elif data_type.lower() in ['float', 'decimal', 'number']:
                    dynamic_columns.append(Column(col_name, Float))
                elif data_type.lower() in ['datetime', 'date']:
                    dynamic_columns.append(Column(col_name, DateTime))
                elif data_type.lower() in ['text', 'longtext']:
                    dynamic_columns.append(Column(col_name, Text))
                else:
                    # Default to String for unknown types
                    dynamic_columns.append(Column(col_name, String))

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
        identifier_columns = ['season_id', 'crop_id', 'variety_id', 'grower_id', 'lot_id']
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
        Convert Pandas NaN to None and ensure all values are valid Python native types
        """
        sanitized = {}
        for k, v in record.items():
            if isinstance(v, float) and np.isnan(v):
                sanitized[k] = None
            elif isinstance(v, pd.Timestamp):
                sanitized[k] = v.to_pydatetime()  # Convert to datetime object (not string)
            else:
                sanitized[k] = v
        return sanitized

    def bulk_insert_inspection_level_data(self, level_dataframes: Dict[int, pd.DataFrame],
                                          base_inspection_mapping: Dict[str, str]) -> Dict[int, int]:
        """
        Bulk insert inspection level data into respective tables

        Args:
            level_dataframes: Dict of level -> DataFrame
            base_inspection_mapping: Mapping from key to base_inspection_id

        Returns:
            Dict of level -> number of records inserted
        """
        inserted_counts = {}

        for level, level_df in level_dataframes.items():
            try:
                # Get the table for this level
                table_name = f"inspection_level_{level}"
                table = self.dynamic_model_factory.created_tables.get(table_name)

                if not isinstance(table, Table):
                    logger.warning(f"Table not found or invalid for inspection level {level}")
                    inserted_counts[level] = 0
                    continue

                records = []
                for _, row in level_df.iterrows():
                    record = row.to_dict()

                    # Create a key to find the base inspection ID
                    base_key = self._create_base_inspection_key(record)
                    base_inspection_id = base_inspection_mapping.get(base_key)

                    if base_inspection_id:
                        record['base_inspection_id'] = base_inspection_id

                    # Clean and sanitize the record
                    cleaned_record = self._clean_inspection_record(record, level)
                    sanitized_record = self.sanitize_record(cleaned_record)
                    records.append(sanitized_record)

                # Bulk insert
                if records:
                    insert_stmt = table.insert().values(records)
                    self.db.execute(insert_stmt)
                    self.db.commit()
                    inserted_counts[level] = len(records)
                    logger.info(f"Inserted {len(records)} records for inspection level {level}")
                else:
                    inserted_counts[level] = 0
                    logger.warning(f"No records to insert for inspection level {level}")

            except Exception as e:
                logger.error(f"Error inserting inspection level {level} data: {str(e)}")
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

        # Keep only the keys that match table columns and drop nulls
        cleaned = {
            key: value
            for key, value in record.items()
            if key in valid_columns and pd.notnull(value)
        }

        return cleaned
