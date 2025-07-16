import pandas as pd
import logging
from typing import Dict, List, Any
import re

logger = logging.getLogger(__name__)


class YieldService:
    """Service for processing yield-related data from inspection records."""

    def __init__(self):
        self.yield_columns_pattern = r'yield_'

    def _find_yield_columns(self, df: pd.DataFrame) -> List[str]:
        """Find all yield-related columns in the DataFrame."""
        yield_columns = []
        for col in df.columns:
            if re.match(self.yield_columns_pattern, col, re.IGNORECASE):
                # Exclude 'yield_production_plant' as it's used in organizer data
                yield_columns.append(col)
        return yield_columns

    def _normalize_keys(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize dictionary keys by converting to lowercase and replacing spaces/hyphens with underscores."""
        normalized = {}
        for key, value in record.items():
            if key:
                # Convert to lowercase and replace spaces/hyphens with underscores
                normalized_key = key.lower().strip().replace(' ', '_').replace('-', '_')
                # Remove leading/trailing underscores
                normalized_key = normalized_key.strip('_')
                normalized[normalized_key] = value
        return normalized

    def extract_yield_data(self, final_df: pd.DataFrame) -> Dict[str, List[Dict]]:
        """
        Extract yield data from final_df with required identifiers.

        Args:
            final_df: DataFrame containing processed inspection data with yield columns

        Returns:
            Dictionary containing yield records with required identifiers
        """
        try:
            # Find yield-related columns
            yield_columns = self._find_yield_columns(final_df)

            if not yield_columns:
                logger.warning("No yield columns found in the DataFrame")
                return {'yield_data': []}

            logger.info(f"Found yield columns: {yield_columns}")

            # Required identifier columns
            required_columns = [
                'grower_id', 'crop_id', 'lot_id', 'season_id', 'variety_id'
            ]

            # Check if all required columns exist
            missing_columns = [col for col in required_columns if col not in final_df.columns]
            if missing_columns:
                logger.error(f"Missing required columns: {missing_columns}")
                raise ValueError(f"Missing required columns: {missing_columns}")

            # Select required columns + yield columns
            selected_columns = required_columns + yield_columns

            # Extract yield data
            yield_df = final_df[selected_columns].copy()

            # Drop rows where all yield columns are null/empty
            yield_df = yield_df.dropna(subset=yield_columns, how='all')

            yield_df.columns = [col.strip("yield_") if re.search(r'(?i)yield_', col) else col for col in yield_df.columns]

            # Convert to records
            yield_records = yield_df.to_dict('records')

            # Normalize keys
            normalized_records = [self._normalize_keys(record) for record in yield_records]

            # Filter out records with empty required identifiers
            valid_records = []
            for record in normalized_records:
                if all(record.get(col) for col in required_columns):
                    valid_records.append(record)
                else:
                    logger.debug(f"Skipping record with missing identifiers: {record}")

            logger.info(f"Extracted {len(valid_records)} valid yield records")

            return {'yield_data': valid_records}

        except Exception as e:
            logger.error(f"Error extracting yield data: {str(e)}")
            raise ValueError(f"Failed to extract yield data: {str(e)}")

    def process_yield_data(self, final_df: pd.DataFrame, engine=None) -> Dict[str, Any]:
        """
        Process yield data and optionally save to database.

        Args:
            final_df: DataFrame containing processed inspection data
            engine: Database engine for saving data (optional)

        Returns:
            Dictionary containing processed yield data and summary
        """
        try:
            # Extract yield data
            yield_data = self.extract_yield_data(final_df)

            # Create summary
            summary = {
                'total_yield_records': len(yield_data['yield_data']),
                'yield_columns_found': self._find_yield_columns(final_df),
                'processing_status': 'success'
            }

            # If database engine is provided, save the data
            if engine:
                success = self._save_yield_data(yield_data['yield_data'], engine)
                summary['database_save_status'] = 'success' if success else 'failed'

            result = {
                'yield_data': yield_data['yield_data'],
                'summary': summary
            }

            logger.info(f"Yield processing completed: {summary}")

            return result

        except Exception as e:
            logger.error(f"Error processing yield data: {str(e)}")
            raise

    def _save_yield_data(self, yield_records: List[Dict], engine) -> bool:
        """
        Save yield data to database.

        Args:
            yield_records: List of yield records
            engine: Database engine

        Returns:
            Boolean indicating success
        """
        try:
            if not yield_records:
                logger.warning("No yield records to save")
                return True

            # Convert to DataFrame
            yield_df = pd.DataFrame(yield_records)

            # Save to database (adjust table name as needed)
            table_name = 'yield_data'  # Adjust this based on your schema
            yield_df.to_sql(table_name, engine, if_exists='append', index=False)

            logger.info(f"Successfully saved {len(yield_records)} yield records to {table_name}")
            return True

        except Exception as e:
            logger.error(f"Error saving yield data to database: {str(e)}")
            return False

    def get_yield_summary(self, yield_data: List[Dict]) -> Dict[str, Any]:
        """
        Generate summary statistics for yield data.

        Args:
            yield_data: List of yield records

        Returns:
            Dictionary containing summary statistics
        """
        try:
            if not yield_data:
                return {'message': 'No yield data available'}

            df = pd.DataFrame(yield_data)

            # Find numeric yield columns
            numeric_columns = df.select_dtypes(include=['number']).columns
            yield_numeric_cols = [col for col in numeric_columns if 'yield' in col.lower()]

            summary = {
                'total_records': len(yield_data),
                'unique_growers': df['grower_id'].nunique() if 'grower_id' in df.columns else 0,
                'unique_crops': df['crop_id'].nunique() if 'crop_id' in df.columns else 0,
                'unique_varieties': df['variety_id'].nunique() if 'variety_id' in df.columns else 0,
                'unique_seasons': df['season_id'].nunique() if 'season_id' in df.columns else 0,
                'yield_statistics': {}
            }

            # Calculate statistics for yield columns
            for col in yield_numeric_cols:
                if col in df.columns:
                    summary['yield_statistics'][col] = {
                        'mean': df[col].mean(),
                        'median': df[col].median(),
                        'min': df[col].min(),
                        'max': df[col].max(),
                        'std': df[col].std(),
                        'count': df[col].count()
                    }

            return summary

        except Exception as e:
            logger.error(f"Error generating yield summary: {str(e)}")
            return {'error': str(e)}


# Usage example function
def create_yield_service():
    """Factory function to create YieldService instance."""
    return YieldService()

