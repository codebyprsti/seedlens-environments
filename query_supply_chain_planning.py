"""
Script to query and analyze the supply_chain_planning table.
Shows table statistics, sample data, and common queries.
"""
import sys
import os
import logging
from datetime import datetime

# Fix DB_HOST environment variable if it's set incorrectly
if os.getenv('DB_HOST') == '127.0.0.1':
    os.environ['DB_HOST'] = '10.8.0.1'

from core.db import get_connection, release_connection
from core.config import settings
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def query_table_stats():
    """Get basic statistics about the table"""
    conn = None
    try:
        logger.info("=" * 80)
        logger.info("QUERYING SUPPLY_CHAIN_PLANNING TABLE")
        logger.info("=" * 80)
        logger.info(f"Database: {settings.DB_NAME}")
        logger.info(f"Host: {settings.DB_HOST}")
        logger.info("=" * 80)
        
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        # 1. Total row count
        logger.info("\n1. TABLE STATISTICS")
        logger.info("-" * 80)
        cur.execute("SELECT COUNT(*) as total_rows FROM operations.supply_chain_planning")
        total_rows = cur.fetchone()['total_rows']
        logger.info(f"Total rows: {total_rows:,}")
        
        # 2. Count by plan_revision_version
        logger.info("\n2. ROWS BY PLAN REVISION VERSION")
        logger.info("-" * 80)
        cur.execute("""
            SELECT plan_revision_version, COUNT(*) as count
            FROM operations.supply_chain_planning
            GROUP BY plan_revision_version
            ORDER BY count DESC
        """)
        versions = cur.fetchall()
        for row in versions:
            logger.info(f"  {row['plan_revision_version']}: {row['count']:,} rows")
        
        # 3. Count by season
        logger.info("\n3. ROWS BY SEASON")
        logger.info("-" * 80)
        cur.execute("""
            SELECT season, COUNT(*) as count
            FROM operations.supply_chain_planning
            WHERE season IS NOT NULL
            GROUP BY season
            ORDER BY count DESC
            LIMIT 10
        """)
        seasons = cur.fetchall()
        for row in seasons:
            logger.info(f"  {row['season']}: {row['count']:,} rows")
        
        # 4. Count by crop
        logger.info("\n4. ROWS BY CROP")
        logger.info("-" * 80)
        cur.execute("""
            SELECT crop, COUNT(*) as count
            FROM operations.supply_chain_planning
            WHERE crop IS NOT NULL
            GROUP BY crop
            ORDER BY count DESC
            LIMIT 10
        """)
        crops = cur.fetchall()
        for row in crops:
            logger.info(f"  {row['crop']}: {row['count']:,} rows")
        
        # 5. Count by state
        logger.info("\n5. ROWS BY STATE")
        logger.info("-" * 80)
        cur.execute("""
            SELECT state, COUNT(*) as count
            FROM operations.supply_chain_planning
            WHERE state IS NOT NULL AND state != ''
            GROUP BY state
            ORDER BY count DESC
            LIMIT 10
        """)
        states = cur.fetchall()
        for row in states:
            logger.info(f"  {row['state']}: {row['count']:,} rows")
        
        # 6. Column information
        logger.info("\n6. TABLE COLUMNS")
        logger.info("-" * 80)
        cur.execute("""
            SELECT column_name, data_type, character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = 'operations' 
            AND table_name = 'supply_chain_planning'
            ORDER BY ordinal_position
        """)
        columns = cur.fetchall()
        logger.info(f"Total columns: {len(columns)}")
        for row in columns[:20]:  # Show first 20 columns
            col_info = f"  {row['column_name']}: {row['data_type']}"
            if row['character_maximum_length']:
                col_info += f"({row['character_maximum_length']})"
            logger.info(col_info)
        if len(columns) > 20:
            logger.info(f"  ... and {len(columns) - 20} more columns")
        
        # 7. Sample data
        logger.info("\n7. SAMPLE DATA (First 5 rows)")
        logger.info("-" * 80)
        cur.execute("""
            SELECT 
                plan_revision_version,
                season,
                crop,
                variety,
                state,
                village,
                grower,
                production_allocation,
                actual_net_acres,
                productivity
            FROM operations.supply_chain_planning
            LIMIT 5
        """)
        samples = cur.fetchall()
        for idx, row in enumerate(samples, 1):
            logger.info(f"\n  Row {idx}:")
            logger.info(f"    Version: {row['plan_revision_version']}")
            logger.info(f"    Season: {row['season']}")
            logger.info(f"    Crop: {row['crop']}")
            logger.info(f"    Variety: {row['variety']}")
            logger.info(f"    State: {row['state']}")
            logger.info(f"    Village: {row['village']}")
            logger.info(f"    Grower: {row['grower']}")
            logger.info(f"    Production Allocation: {row['production_allocation']}")
            logger.info(f"    Actual Net Acres: {row['actual_net_acres']}")
            logger.info(f"    Productivity: {row['productivity']}")
        
        # 8. Aggregate statistics
        logger.info("\n8. AGGREGATE STATISTICS")
        logger.info("-" * 80)
        cur.execute("""
            SELECT 
                COUNT(*) as total_rows,
                COUNT(DISTINCT plan_revision_version) as unique_versions,
                COUNT(DISTINCT season) as unique_seasons,
                COUNT(DISTINCT crop) as unique_crops,
                COUNT(DISTINCT variety) as unique_varieties,
                COUNT(DISTINCT state) as unique_states,
                COUNT(DISTINCT village) as unique_villages,
                SUM(production_allocation) as total_production_allocation,
                SUM(actual_net_acres) as total_actual_net_acres,
                AVG(productivity) as avg_productivity
            FROM operations.supply_chain_planning
            WHERE plan_revision_version IS NOT NULL
        """)
        stats = cur.fetchone()
        logger.info(f"  Total Rows: {stats['total_rows']:,}")
        logger.info(f"  Unique Versions: {stats['unique_versions']}")
        logger.info(f"  Unique Seasons: {stats['unique_seasons']}")
        logger.info(f"  Unique Crops: {stats['unique_crops']}")
        logger.info(f"  Unique Varieties: {stats['unique_varieties']}")
        logger.info(f"  Unique States: {stats['unique_states']}")
        logger.info(f"  Unique Villages: {stats['unique_villages']}")
        logger.info(f"  Total Production Allocation: {stats['total_production_allocation']:,.2f}")
        logger.info(f"  Total Actual Net Acres: {stats['total_actual_net_acres']:,.2f}")
        logger.info(f"  Average Productivity: {stats['avg_productivity']:.2f}")
        
        # 9. Check indexes
        logger.info("\n9. INDEXES ON TABLE")
        logger.info("-" * 80)
        cur.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'operations' 
            AND tablename = 'supply_chain_planning'
            ORDER BY indexname
        """)
        indexes = cur.fetchall()
        if indexes:
            logger.info(f"Found {len(indexes)} indexes:")
            for row in indexes:
                logger.info(f"  • {row['indexname']}")
        else:
            logger.warning("  No indexes found! Run 'python create_indexes.py' to create indexes for better performance.")
        
        cur.close()
        release_connection(conn)
        
        logger.info("\n" + "=" * 80)
        logger.info("QUERY COMPLETE!")
        logger.info("=" * 80)
        
        return True
        
    except Exception as e:
        logger.error(f"Error querying table: {str(e)}", exc_info=True)
        if conn:
            try:
                conn.rollback()
                release_connection(conn)
            except:
                pass
        return False

def query_by_version(version: str = None):
    """Query data for a specific plan revision version"""
    conn = None
    try:
        if not version:
            # Get latest version
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT plan_revision_version, COUNT(*) as count
                FROM operations.supply_chain_planning
                GROUP BY plan_revision_version
                ORDER BY plan_revision_version DESC
                LIMIT 1
            """)
            result = cur.fetchone()
            if result:
                version = result[0]
                logger.info(f"Using latest version: {version}")
            cur.close()
            release_connection(conn)
        
        if not version:
            logger.warning("No data found in table")
            return
        
        logger.info("\n" + "=" * 80)
        logger.info(f"QUERYING DATA FOR VERSION: {version}")
        logger.info("=" * 80)
        
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        cur.execute("""
            SELECT 
                season,
                crop,
                variety,
                state,
                COUNT(*) as row_count,
                SUM(production_allocation) as total_production_allocation,
                SUM(actual_net_acres) as total_actual_net_acres,
                AVG(productivity) as avg_productivity
            FROM operations.supply_chain_planning
            WHERE plan_revision_version = %s
            GROUP BY season, crop, variety, state
            ORDER BY season, crop, state
            LIMIT 20
        """, (version,))
        
        results = cur.fetchall()
        logger.info(f"\nFound {len(results)} unique combinations:\n")
        for row in results:
            logger.info(f"  {row['season']} | {row['crop']} | {row['variety']} | {row['state']}")
            logger.info(f"    Rows: {row['row_count']}, Production: {row['total_production_allocation']:,.2f}, Acres: {row['total_actual_net_acres']:,.2f}")
        
        cur.close()
        release_connection(conn)
        
    except Exception as e:
        logger.error(f"Error querying by version: {str(e)}", exc_info=True)
        if conn:
            try:
                conn.rollback()
                release_connection(conn)
            except:
                pass

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Query supply_chain_planning table')
    parser.add_argument('--version', type=str, help='Filter by plan_revision_version')
    args = parser.parse_args()
    
    success = query_table_stats()
    
    if args.version:
        query_by_version(args.version)
    elif success:
        query_by_version()  # Query latest version
    
    if success:
        print("\n[SUCCESS] Query completed successfully!")
    else:
        print("\n[ERROR] Query failed. Check logs for details.")
        sys.exit(1)

