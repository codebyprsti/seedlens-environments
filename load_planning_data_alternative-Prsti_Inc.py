"""
Alternative script to load planning data - allows custom file path input.
"""
import sys
import os
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
    """Main function to load planning data with flexible file path."""
    
    # Default path
    default_path = r"C:\Users\madan\OneDrive\Documents\PRSTI Village Onboarding.xlsx"
    
    # Check if file path provided as command line argument
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    else:
        file_path = default_path
    
    # Also check for a copied version in the project directory
    alternative_paths = [
        file_path,
        os.path.join(os.getcwd(), "PRSTI Village Onboarding.xlsx"),
        os.path.join(os.getcwd(), "temp_planning_data.xlsx"),
        r"C:\Users\madan\Desktop\PRSTI Village Onboarding.xlsx",
    ]
    
    # Find the first accessible file
    found_path = None
    for path in alternative_paths:
        if os.path.exists(path):
            try:
                # Try to open it
                test = open(path, 'rb')
                test.close()
                found_path = path
                logger.info(f"Found accessible file at: {path}")
                break
            except PermissionError:
                logger.warning(f"File exists but is locked: {path}")
                continue
    
    if not found_path:
        print("\n[ERROR] Could not find an accessible Excel file.")
        print("\nPlease do one of the following:")
        print("1. Close Excel and OneDrive, then run again")
        print("2. Copy the file to the project directory and run:")
        print(f"   python load_planning_data_alternative.py <path_to_file>")
        print("3. Copy the file to Desktop and update the script")
        sys.exit(1)
    
    plan_revision_version = "v4.0-Manual"
    
    db = SessionLocal()
    try:
        logger.info("=" * 80)
        logger.info("Starting Planning Data Load Process")
        logger.info(f"File: {found_path}")
        logger.info(f"Plan Revision Version: {plan_revision_version}")
        logger.info("=" * 80)
        
        # Initialize loader
        loader = PlanningDataLoader(db)
        
        # Delete existing data for this version to avoid duplicates
        logger.info(f"Deleting existing data for version: {plan_revision_version}")
        from core.db import get_connection, release_connection
        import psycopg2.extras
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            cur.execute("""
                DELETE FROM operations.supply_chain_planning 
                WHERE plan_revision_version = %s
            """, (plan_revision_version,))
            deleted_count = cur.rowcount
            conn.commit()
            logger.info(f"Deleted {deleted_count} existing rows for version {plan_revision_version}")
        except Exception as e:
            logger.warning(f"Error deleting existing data: {e}")
            conn.rollback()
        finally:
            cur.close()
            release_connection(conn)
        
        # Load and process data
        result = loader.load_and_process_planning_data(
            file_path=found_path,
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
            print("\n[SUCCESS] Planning data loaded successfully!")
            print(f"   - Inserted {result.get('rows_inserted', 0)} rows")
            print(f"   - Created {result.get('columns_created', 0)} new columns")
            print(f"   - Created {result.get('master_entities_created', 0)} master entities")
            
    except FileNotFoundError as e:
        logger.error(f"File not found: {found_path}")
        logger.error(f"Error: {str(e)}")
        print(f"\n[ERROR] File not found at {found_path}")
        sys.exit(1)
    except PermissionError as e:
        logger.error(f"Permission error: {str(e)}")
        print(f"\n[ERROR] Permission Error: {str(e)}")
        print("\n[INFO] Solution:")
        print("   1. Close the Excel file if it's open")
        print("   2. Wait for OneDrive sync to complete")
        print("   3. Copy the file to a different location (e.g., Desktop)")
        print("   4. Run: python load_planning_data_alternative.py <new_path>")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        print(f"\n[ERROR] Error: {str(e)}")
        sys.exit(1)
    finally:
        db.close()
        logger.info("Database session closed")

if __name__ == "__main__":
    main()

