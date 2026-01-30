"""
Script to load V4.0 manual planning data from Excel into supply_chain_planning table.
"""
import sys
import logging
from core.db import SessionLocal
from services.record_service import PlanningDataLoader

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    """Main function to load planning data."""
    import time
    import os
    
    # Try multiple possible file paths
    possible_paths = [
        r"C:\Users\madan\OneDrive\Documents\PRSTI Village Onboarding.xlsx",
        r"C:\Users\madan\Documents\PRSTI Village Onboarding.xlsx",
        r"C:\Users\madan\OneDrive\Desktop\PRSTI Village Onboarding.xlsx",
        r"C:\Users\madan\OneDrive\Desktop\ec2_copiedversion_seediq\SeedIQ-Prod\temp_planning_data.xlsx",
    ]
    
    file_path = None
    for path in possible_paths:
        if os.path.exists(path):
            file_path = path
            break
    
    if not file_path:
        logger.error("Could not find the Excel file. Please check the file path.")
        print("\n❌ Error: Excel file not found.")
        print("Please ensure the file exists at one of these locations:")
        for path in possible_paths:
            print(f"   - {path}")
        sys.exit(1)
    
    plan_revision_version = "V4.0"
    
    # Note: We'll let pandas/openpyxl handle file access directly
    # The file might be readable even if it shows as locked initially
    
    db = SessionLocal()
    try:
        logger.info("=" * 80)
        logger.info("Starting Planning Data Load Process")
        logger.info(f"File: {file_path}")
        logger.info(f"Plan Revision Version: {plan_revision_version}")
        logger.info("=" * 80)
        
        # Initialize loader
        loader = PlanningDataLoader(db)
        
        # Load and process data
        result = loader.load_and_process_planning_data(
            file_path=file_path,
            plan_revision_version=plan_revision_version
        )
        
        # Print results
        logger.info("=" * 80)
        logger.info("Processing Complete!")
        logger.info("=" * 80)
        logger.info(f"Success: {result.get('success', False)}")
        logger.info(f"Rows Processed: {result.get('rows_processed', 0)}")
        logger.info(f"Rows Inserted: {result.get('rows_inserted', 0)}")
        logger.info(f"Rows Skipped: {result.get('rows_skipped', 0)}")
        logger.info(f"Columns Created: {result.get('columns_created', 0)}")
        logger.info(f"Master Entities Created: {result.get('master_entities_created', 0)}")
        
        if result.get('error'):
            logger.error(f"Error: {result.get('error')}")
            sys.exit(1)
        else:
            logger.info(f"Message: {result.get('message', 'N/A')}")
            logger.info("=" * 80)
            print("\n✅ Planning data loaded successfully!")
            print(f"   - Inserted {result.get('rows_inserted', 0)} rows")
            print(f"   - Created {result.get('columns_created', 0)} new columns")
            print(f"   - Created {result.get('master_entities_created', 0)} master entities")
            
    except FileNotFoundError as e:
        logger.error(f"File not found: {file_path}")
        logger.error(f"Error: {str(e)}")
        print(f"\n❌ Error: File not found at {file_path}")
        sys.exit(1)
    except PermissionError as e:
        logger.error(f"Permission error: {str(e)}")
        print(f"\n❌ Permission Error: {str(e)}")
        print("\n💡 Solution:")
        print("   1. Close the Excel file if it's open")
        print("   2. Wait for OneDrive sync to complete")
        print("   3. Check if another program is using the file")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        print(f"\n❌ Error: {str(e)}")
        sys.exit(1)
    finally:
        db.close()
        logger.info("Database session closed")

if __name__ == "__main__":
    main()

