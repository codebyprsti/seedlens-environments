"""
Script to load Inspection Reports Production RABI 21-25 Excel file with 6 sheets into dataframes.
"""
import os
import pandas as pd
import logging
from typing import Dict, Optional
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


class InspectionReportsLoader:
    """
    Class to load and process Inspection Reports Production Excel file.
    Loads all sheets into dataframes for further processing.
    """
    
    def __init__(self, file_path: str):
        """
        Initialize the loader with file path.
        
        Args:
            file_path: Path to the Excel file
        """
        self.file_path = file_path
        self.dataframes: Dict[str, pd.DataFrame] = {}
        self.sheet_names: list = []
        
        # Validate file exists
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Excel file not found: {file_path}")
        
        logger.info(f"Initialized InspectionReportsLoader with file: {file_path}")
    
    def load_all_sheets(self) -> Dict[str, pd.DataFrame]:
        """
        Load all sheets from the Excel file into dataframes.
        
        Returns:
            Dictionary mapping sheet names to DataFrames
        """
        try:
            logger.info("Starting to load Excel sheets...")
            
            # Read all sheets - sheet_name=None returns a dict of all sheets
            excel_data = pd.read_excel(
                self.file_path,
                sheet_name=None,  # Read all sheets
                engine='openpyxl'
            )
            
            self.sheet_names = list(excel_data.keys())
            self.dataframes = excel_data
            
            logger.info(f"Successfully loaded {len(self.dataframes)} sheets:")
            for sheet_name, df in self.dataframes.items():
                logger.info(f"  - {sheet_name}: {df.shape[0]} rows, {df.shape[1]} columns")
                # Remove completely empty rows
                self.dataframes[sheet_name] = df.dropna(how='all')
            
            return self.dataframes
            
        except PermissionError as e:
            logger.error(f"Permission error: File may be locked or inaccessible")
            logger.error(f"Please ensure:")
            logger.error(f"  1. The file is not open in Excel")
            logger.error(f"  2. OneDrive sync is complete")
            logger.error(f"  3. No other process is using the file")
            raise
            
        except ImportError as e:
            error_msg = str(e).lower()
            if 'openpyxl' in error_msg:
                raise ImportError(
                    "openpyxl is required to read Excel files. "
                    "Please install it using: pip install openpyxl"
                ) from e
            raise
            
        except Exception as e:
            logger.error(f"Error loading Excel file: {str(e)}")
            raise
    
    def get_sheet(self, sheet_name: str) -> Optional[pd.DataFrame]:
        """
        Get a specific sheet dataframe by name.
        
        Args:
            sheet_name: Name of the sheet to retrieve
            
        Returns:
            DataFrame for the requested sheet, or None if not found
        """
        if not self.dataframes:
            logger.warning("No sheets loaded yet. Call load_all_sheets() first.")
            return None
        
        if sheet_name not in self.dataframes:
            logger.warning(f"Sheet '{sheet_name}' not found. Available sheets: {self.sheet_names}")
            return None
        
        return self.dataframes[sheet_name]
    
    def get_all_sheet_names(self) -> list:
        """
        Get list of all sheet names.
        
        Returns:
            List of sheet names
        """
        return self.sheet_names.copy()
    
    def get_dataframe_info(self) -> Dict[str, Dict]:
        """
        Get information about all dataframes (shape, columns, etc.).
        
        Returns:
            Dictionary with info about each sheet
        """
        info = {}
        for sheet_name, df in self.dataframes.items():
            info[sheet_name] = {
                'rows': len(df),
                'columns': len(df.columns),
                'column_names': list(df.columns),
                'memory_usage': df.memory_usage(deep=True).sum()
            }
        return info
    
    def print_summary(self):
        """Print a summary of all loaded sheets."""
        print("\n" + "=" * 80)
        print("INSPECTION REPORTS - LOADED SHEETS SUMMARY")
        print("=" * 80)
        
        if not self.dataframes:
            print("No sheets loaded yet. Call load_all_sheets() first.")
            return
        
        for idx, sheet_name in enumerate(self.sheet_names, 1):
            df = self.dataframes[sheet_name]
            print(f"\nSheet {idx}: {sheet_name}")
            print(f"  - Rows: {len(df):,}")
            print(f"  - Columns: {len(df.columns)}")
            print(f"  - Column names: {', '.join(df.columns[:5].tolist())}" + 
                  (f"... ({len(df.columns) - 5} more)" if len(df.columns) > 5 else ""))
        
        print("\n" + "=" * 80)
    
    # ============================================================================
    # ADD YOUR METHODS HERE
    # ============================================================================
    
    def process_data(self):
        """
        Placeholder method for data processing.
        Override this method to add your custom processing logic.
        """
        logger.info("Processing data...")
        # TODO: Add your processing logic here
        pass
    
    def export_to_database(self, db_session=None):
        """
        Placeholder method for exporting data to database.
        Override this method to add your database export logic.
        
        Args:
            db_session: Database session (optional)
        """
        logger.info("Exporting to database...")
        # TODO: Add your database export logic here
        pass
    
    def validate_data(self) -> Dict[str, bool]:
        """
        Placeholder method for data validation.
        Override this method to add your validation logic.
        
        Returns:
            Dictionary mapping sheet names to validation status
        """
        logger.info("Validating data...")
        validation_results = {}
        
        # TODO: Add your validation logic here
        for sheet_name in self.sheet_names:
            validation_results[sheet_name] = True  # Placeholder
        
        return validation_results


def main():
    """Main function to load and process inspection reports."""
    
    # Excel file path
    excel_path = r"C:\Users\madan\OneDrive\Documents\Inspection Reports Production RABI 21-25 decoded (1).xlsx"
    
    try:
        # Initialize loader
        loader = InspectionReportsLoader(excel_path)
        
        # Load all sheets into dataframes
        dataframes = loader.load_all_sheets()
        
        # Print summary
        loader.print_summary()
        
        # Example: Access specific sheets
        # sheet_1 = loader.get_sheet(sheet_name="Sheet1")
        # sheet_2 = loader.get_sheet(sheet_name="Sheet2")
        # ... etc
        
        # Get dataframe info
        info = loader.get_dataframe_info()
        logger.info(f"Loaded {len(info)} sheets successfully")
        
        # TODO: Add your custom processing methods here
        # loader.process_data()
        # loader.validate_data()
        # loader.export_to_database()
        
        print("\n[SUCCESS] Excel file loaded successfully!")
        print(f"   - Total sheets: {len(dataframes)}")
        print(f"   - Sheet names: {', '.join(loader.get_all_sheet_names())}")
        
        return loader
        
    except FileNotFoundError as e:
        logger.error(f"File not found: {excel_path}")
        print(f"\n[ERROR] File not found: {excel_path}")
        print("Please check the file path and try again.")
        
    except PermissionError as e:
        logger.error(f"Permission error: {str(e)}")
        print(f"\n[ERROR] Permission Error: {str(e)}")
        print("\n[INFO] Solution:")
        print("   1. Close the Excel file if it's open")
        print("   2. Wait for OneDrive sync to complete")
        print("   3. Copy the file to a different location if needed")
        
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        print(f"\n[ERROR] Error: {str(e)}")


if __name__ == "__main__":
    loader = main()


