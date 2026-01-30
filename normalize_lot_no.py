"""
Normalize lot_no in inspection_level_1 by applying lower(trim())
This updates existing rows without reloading the table
"""
from core.db import SessionLocal
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def normalize_inspection_level_1():
    """Normalize lot_no in inspection_level_1 using UPDATE"""
    db = SessionLocal()
    try:
        logger.info("Normalizing lot_no in inspection_level_1...")
        
        # Update lot_no to normalized form: lower(trim(lot_no))
        # Handle empty strings → NULL
        update_sql = """
        UPDATE operations.inspection_level_1
        SET lot_no = CASE 
            WHEN TRIM(lot_no) = '' THEN NULL
            ELSE LOWER(TRIM(lot_no))
        END
        WHERE lot_no IS NOT NULL;
        """
        
        result = db.execute(text(update_sql))
        db.commit()
        
        updated_count = result.rowcount
        logger.info(f"✓ Updated {updated_count:,} rows in inspection_level_1")
        
        # Verify normalization
        verify_sql = """
        SELECT 
            COUNT(*) as total_rows,
            COUNT(lot_no) as non_null_lot_no,
            COUNT(DISTINCT lot_no) as distinct_lot_no,
            COUNT(*) FILTER (WHERE lot_no != LOWER(TRIM(lot_no))) as not_normalized
        FROM operations.inspection_level_1;
        """
        
        verify_result = db.execute(text(verify_sql)).fetchone()
        logger.info(f"Verification:")
        logger.info(f"  Total rows: {verify_result[0]:,}")
        logger.info(f"  Non-null lot_no: {verify_result[1]:,}")
        logger.info(f"  Distinct lot_no: {verify_result[2]:,}")
        logger.info(f"  Not normalized: {verify_result[3]:,}")
        
        if verify_result[3] > 0:
            logger.warning(f"WARNING: {verify_result[3]} rows still not normalized!")
        else:
            logger.info("✓ All lot_no values are normalized")
        
    except Exception as e:
        logger.error(f"Error normalizing inspection_level_1: {e}")
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    normalize_inspection_level_1()

