"""
Script to reload yield data using the new reload_yield_data method with composite primary keys
and master data auto-creation.

Usage:
    python reload_yield_data.py <csv_path>
    
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
    """Main function to reload yield data."""
    
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
        print(f"\n[ERROR] File not found: {file_path}")
        print("\nPlease provide a valid file path:")
        print("   python reload_yield_data.py <file_path>")
        sys.exit(1)
    
    logger.info("=" * 80)
    logger.info("SAFE YIELD DATA RELOAD (Schema Preserved)")
    logger.info("=" * 80)
    logger.info(f"File: {file_path}")
    logger.info(f"File exists: {os.path.exists(file_path)}")
    logger.info(f"File size: {os.path.getsize(file_path):,} bytes")
    logger.info("=" * 80)
    
    db = SessionLocal()
    try:
        # Initialize RecordService (skip init cache - reload will preload masters itself)
        service = RecordService(db, skip_init_cache=True)
        
        # Reload yield data with master data auto-creation (SAFE - no schema changes)
        logger.info("Starting SAFE yield data reload...")
        logger.info("This will:")
        logger.info("  1. TRUNCATE existing data (schema preserved)")
        logger.info("  2. Load CSV and normalize columns")
        logger.info("  3. Resolve master data IDs (auto-create if missing)")
        logger.info("  4. Split data into inspection_base and yield tables")
        logger.info("  5. Bulk insert with conflict handling")
        logger.info("")
        logger.info("NOTE: Master tables are NEVER modified - only lookups and new entry creation")
        logger.info("")
        
        # SAFE RELOAD: truncate_data=True clears existing data, no schema changes
        stats = service.reload_yield_data(file_path, truncate_data=True)
        
        # Print summary
        logger.info("=" * 80)
        logger.info("RELOAD SUMMARY")
        logger.info("=" * 80)
        logger.info(f"CSV Rows Loaded: {stats['csv_rows_loaded']:,}")
        logger.info(f"Rows Truncated (before reload): {stats.get('rows_truncated', 0):,}")
        logger.info(f"Inspection Records Inserted: {stats['inspection_records_inserted']:,}")
        logger.info(f"Yield Records Inserted: {stats['yield_records_inserted']:,}")
        if any(stats['master_records_created'].values()):
            logger.info(f"Master Records Created:")
            for master_type, count in stats['master_records_created'].items():
                if count > 0:
                    logger.info(f"  - {master_type.capitalize()}: {count}")
        if stats['errors']:
            logger.warning(f"Errors: {len(stats['errors'])}")
            for error in stats['errors']:
                logger.warning(f"  - {error}")
        logger.info("=" * 80)
        
        # --- EXECUTION SUMMARY (performance) ---
        total = stats.get('timing', {}).get('total', 0)
        timing = stats.get('timing', {})
        print("\n" + "=" * 70)
        print("EXECUTION SUMMARY (Performance)")
        print("=" * 70)
        print(f"  Total runtime:        {total:.3f}s")
        print(f"  Rows loaded:          {stats['csv_rows_loaded']}")
        print(f"  Throughput:           {stats['csv_rows_loaded']/max(total,0.001):.0f} rows/sec")
        print("-" * 70)
        print("  Time per stage:")
        for step in ['recreate_tables', 'preload_masters', 'load_csv', 'process_data', 'create_masters', 'bulk_insert', 'total']:
            if step in timing and timing[step] is not None:
                pct = (timing[step] / total * 100) if total else 0
                print(f"    {step:20s} {timing[step]:8.3f}s  ({pct:5.1f}%)")
        if stats.get('profiling'):
            print("-" * 70)
            print("  DB / Python breakdown (bottlenecks):")
            for k in sorted(stats['profiling'].keys()):
                v = stats['profiling'][k]
                if isinstance(v, (int, float)) and v > 0:
                    print(f"    {k:35s} {v:.3f}s")
        print("=" * 70)
        
        print("\n[SUCCESS] Yield data reloaded successfully!")
        print(f"   - CSV Rows Loaded: {stats['csv_rows_loaded']}")
        print(f"   - Inspection Records: {stats['inspection_records_inserted']}")
        print(f"   - Yield Records: {stats['yield_records_inserted']}")
        if any(stats['master_records_created'].values()):
            print(f"   - Master Records Created: {sum(stats['master_records_created'].values())}")
        
    except FileNotFoundError as e:
        logger.error(f"File not found: {file_path}")
        print(f"\n[ERROR] File not found: {file_path}")
        print("Please check the file path and try again.")
        sys.exit(1)
        
    except PermissionError as e:
        logger.error(f"Permission error: {str(e)}")
        print(f"\n[ERROR] Permission Error: {str(e)}")
        print("\n[SOLUTION]")
        print("   1. Close the CSV file if it's open")
        print("   2. Wait for OneDrive sync to complete")
        print("   3. Copy the file to a different location if needed")
        sys.exit(1)
        
    except Exception as e:
        logger.error(f"Error reloading data: {str(e)}", exc_info=True)
        print(f"\n[ERROR] {str(e)}")
        print("\nCheck the logs above for detailed error information.")
        import traceback
        traceback.print_exc()
        sys.exit(1)
        
    finally:
        db.close()
        logger.info("Database session closed")


if __name__ == "__main__":
    main()

