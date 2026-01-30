"""
Script to create indexes on supply_chain_planning table for better query performance.
Run this script once to optimize database performance.
"""
import sys
import os
import logging

# Fix DB_HOST environment variable if it's set incorrectly
if os.getenv('DB_HOST') == '127.0.0.1':
    os.environ['DB_HOST'] = '10.8.0.1'

from core.db import get_connection, release_connection
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def create_indexes():
    """Create indexes on frequently queried columns"""
    
    indexes = [
        # Primary filter columns
        {
            'name': 'idx_supply_chain_planning_plan_revision_version',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_plan_revision_version ON operations.supply_chain_planning(plan_revision_version)',
            'description': 'Index on plan_revision_version (most common filter)'
        },
        {
            'name': 'idx_supply_chain_planning_season',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_season ON operations.supply_chain_planning(season)',
            'description': 'Index on season'
        },
        {
            'name': 'idx_supply_chain_planning_crop',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_crop ON operations.supply_chain_planning(crop)',
            'description': 'Index on crop'
        },
        {
            'name': 'idx_supply_chain_planning_variety',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_variety ON operations.supply_chain_planning(variety)',
            'description': 'Index on variety'
        },
        {
            'name': 'idx_supply_chain_planning_state',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_state ON operations.supply_chain_planning(state)',
            'description': 'Index on state'
        },
        {
            'name': 'idx_supply_chain_planning_village',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_village ON operations.supply_chain_planning(village)',
            'description': 'Index on village'
        },
        {
            'name': 'idx_supply_chain_planning_grower',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_grower ON operations.supply_chain_planning(grower)',
            'description': 'Index on grower'
        },
        
        # Composite indexes for common query patterns
        {
            'name': 'idx_supply_chain_planning_season_crop',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_season_crop ON operations.supply_chain_planning(season, crop)',
            'description': 'Composite index on season and crop'
        },
        {
            'name': 'idx_supply_chain_planning_season_crop_state',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_season_crop_state ON operations.supply_chain_planning(season, crop, state)',
            'description': 'Composite index on season, crop, and state'
        },
        {
            'name': 'idx_supply_chain_planning_version_season',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_version_season ON operations.supply_chain_planning(plan_revision_version, season)',
            'description': 'Composite index on plan_revision_version and season'
        },
        
        # ID columns for joins
        {
            'name': 'idx_supply_chain_planning_season_id',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_season_id ON operations.supply_chain_planning(season_id)',
            'description': 'Index on season_id'
        },
        {
            'name': 'idx_supply_chain_planning_crop_id',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_crop_id ON operations.supply_chain_planning(crop_id)',
            'description': 'Index on crop_id'
        },
        {
            'name': 'idx_supply_chain_planning_variety_id',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_supply_chain_planning_variety_id ON operations.supply_chain_planning(variety_id)',
            'description': 'Index on variety_id'
        },
    ]
    
    conn = None
    try:
        logger.info("=" * 80)
        logger.info("Creating indexes on supply_chain_planning table...")
        logger.info("=" * 80)
        
        conn = get_connection()
        cur = conn.cursor()
        
        created_count = 0
        existing_count = 0
        
        for idx in indexes:
            try:
                logger.info(f"Creating index: {idx['name']}")
                logger.info(f"  Description: {idx['description']}")
                
                cur.execute(idx['sql'])
                conn.commit()
                
                # Check if index was created
                cur.execute("""
                    SELECT indexname 
                    FROM pg_indexes 
                    WHERE schemaname = 'operations' 
                    AND tablename = 'supply_chain_planning' 
                    AND indexname = %s
                """, (idx['name'],))
                
                if cur.fetchone():
                    created_count += 1
                    logger.info(f"  ✓ Index created successfully")
                else:
                    existing_count += 1
                    logger.info(f"  ✓ Index already exists")
                    
            except Exception as e:
                logger.error(f"  ✗ Error creating index {idx['name']}: {str(e)}")
                conn.rollback()
                continue
        
        cur.close()
        release_connection(conn)
        
        logger.info("=" * 80)
        logger.info("Index creation complete!")
        logger.info(f"  Created/Updated: {created_count} indexes")
        logger.info(f"  Already existed: {existing_count} indexes")
        logger.info("=" * 80)
        
        # Analyze table to update statistics
        logger.info("Updating table statistics...")
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("ANALYZE operations.supply_chain_planning")
        conn.commit()
        cur.close()
        release_connection(conn)
        logger.info("✓ Table statistics updated")
        
        return True
        
    except Exception as e:
        logger.error(f"Error creating indexes: {str(e)}", exc_info=True)
        if conn:
            try:
                conn.rollback()
                release_connection(conn)
            except:
                pass
        return False

if __name__ == "__main__":
    success = create_indexes()
    if success:
        print("\n[SUCCESS] Indexes created successfully!")
        print("Query performance should be significantly improved.")
    else:
        print("\n[ERROR] Failed to create some indexes. Check logs for details.")
        sys.exit(1)

