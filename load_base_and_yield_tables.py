"""
Script to load base table (season_crop_inspection_base) and yield table (season_crop_yield)
from Excel or CSV file using the refactored RecordService.

Usage:
    python load_base_and_yield_tables.py <file_path>
    
    Or modify the default_path variable below.
"""
import sys
import os
import logging
from core.db import SessionLocal
from services.record_service import RecordService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    """Main function to load base and yield tables."""
    
    # Default file path - modify this or pass as command line argument
    default_path = r"C:\Users\madan\OneDrive\Documents\Yield data RABI 21-25 (Production - Nov25)_updated.csv"
    
    # Check if file path provided as command line argument
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    else:
        file_path = default_path
    
    # Check if file exists
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        print(f"\n❌ Error: File not found: {file_path}")
        print("\nPlease provide a valid file path:")
        print("   python load_base_and_yield_tables.py <file_path>")
        sys.exit(1)
    
    logger.info("=" * 80)
    logger.info("LOADING BASE AND YIELD TABLES")
    logger.info("=" * 80)
    logger.info(f"File: {file_path}")
    logger.info(f"File exists: {os.path.exists(file_path)}")
    logger.info(f"File size: {os.path.getsize(file_path):,} bytes")
    logger.info("=" * 80)
    
    db = SessionLocal()
    try:
        # Initialize RecordService
        service = RecordService(db)
        
        # Load and process Excel/CSV file
        logger.info("Loading and processing file...")
        df, mapping = service.load_and_process_excel(file_path=file_path)
        logger.info(f"✓ Loaded {len(df)} rows, {len(df.columns)} columns")
        
        # Extract unique records and automatically insert base and yield tables
        # Note: extract_unique_records() automatically calls:
        #   - bulk_insert_inspection_base(truncate=True)
        #   - bulk_insert_yield_records(truncate=True)
        logger.info("Extracting unique records and loading into database...")
        logger.info("This will:")
        logger.info("  1. TRUNCATE operations.season_crop_inspection_base")
        logger.info("  2. TRUNCATE operations.season_crop_yield")
        logger.info("  3. Sync table columns (add missing columns dynamically)")
        logger.info("  4. Resolve locations (create new ones if needed)")
        logger.info("  5. Apply fuzzy column mapping")
        logger.info("  6. Clean and validate data")
        logger.info("  7. Bulk insert records")
        logger.info("")
        
        unique_records, final_df = service.extract_unique_records(df)
        
        # Print summary
        logger.info("=" * 80)
        logger.info("LOADING SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Inspection Base Records: {len(unique_records.get('season_crop_inspection_base', []))}")
        logger.info(f"Yield Records: {len(unique_records.get('season_crop_yield', []))}")
        logger.info(f"Processed Counts: {service.processed_counts}")
        logger.info("=" * 80)
        
        print("\n✅ SUCCESS: Base and Yield tables loaded successfully!")
        print(f"   - Inspection Base Records: {service.processed_counts.get('inspection_base', 0)}")
        print(f"   - Yield Records: {service.processed_counts.get('yield', 0)}")
        
    except FileNotFoundError as e:
        logger.error(f"File not found: {file_path}")
        print(f"\n❌ Error: File not found: {file_path}")
        print("Please check the file path and try again.")
        sys.exit(1)
        
    except PermissionError as e:
        logger.error(f"Permission error: {str(e)}")
        print(f"\n❌ Permission Error: {str(e)}")
        print("\n💡 Solution:")
        print("   1. Close the Excel/CSV file if it's open")
        print("   2. Wait for OneDrive sync to complete")
        print("   3. Copy the file to a different location if needed")
        sys.exit(1)
        
    except Exception as e:
        logger.error(f"Error loading data: {str(e)}", exc_info=True)
        print(f"\n❌ Error: {str(e)}")
        print("\nCheck the logs above for detailed error information.")
        sys.exit(1)
        
    finally:
        db.close()
        logger.info("Database session closed")


if __name__ == "__main__":
    main()

