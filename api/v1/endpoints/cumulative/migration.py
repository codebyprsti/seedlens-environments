"""
Migration script to create optimized inspection tables
Creates 6 tables with prefixed columns (s1_, s2_, ..., s6_) for better performance
"""
from sqlalchemy import text, inspect
from core.db import engine, SessionLocal
import logging
from typing import Dict, List
import psycopg2
from core.db import get_connection, release_connection

logger = logging.getLogger(__name__)

# Configure logging for migration script
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

INSPECTION_SCHEMA = "operations"
MAX_LEVEL = 6


def get_all_columns_from_table(conn, table_name: str) -> List[Dict[str, str]]:
    """
    Get all columns from a table with their data types
    
    Args:
        conn: Database connection
        table_name: Name of the table
        
    Returns:
        List of dicts with column_name and data_type
    """
    import psycopg2
    if isinstance(conn, psycopg2.extensions.connection):
        cursor = conn.cursor()
        cursor.execute("""
            SELECT column_name, data_type, character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = %s
            AND table_name = %s
            ORDER BY ordinal_position
        """, (INSPECTION_SCHEMA, table_name))
        columns = []
        for row in cursor.fetchall():
            col_info = {
                'column_name': row[0],
                'data_type': row[1],
                'max_length': row[2]
            }
            columns.append(col_info)
        cursor.close()
        return columns
    else:
        query = text("""
            SELECT column_name, data_type, character_maximum_length
            FROM information_schema.columns
            WHERE table_schema = :schema
            AND table_name = :table_name
            ORDER BY ordinal_position
        """)
        result = conn.execute(query, {"schema": INSPECTION_SCHEMA, "table_name": table_name})
        columns = []
        for row in result:
            col_info = {
                'column_name': row[0],
                'data_type': row[1],
                'max_length': row[2]
            }
            columns.append(col_info)
        return columns


def detect_lot_column(conn, table_name: str) -> str:
    """
    Detect the lot number column name from a table.
    Prefers lot_no over lot_id, and columns that actually have data.
    Uses case-insensitive matching to handle column name variations.
    """
    columns = get_all_columns_from_table(conn, table_name)
    # IMPORTANT: lot_no is the primary key for comparisons, prefer it over lot_id
    possible_lot_columns = ["lot_no", "mrno_lot_no", "lot_number", "lot_id"]
    
    # Create case-insensitive mapping: lowercase -> actual column name
    column_map = {col['column_name'].lower(): col['column_name'] for col in columns}
    
    # Find matching columns (case-insensitive)
    existing_cols = []
    for possible in possible_lot_columns:
        possible_lower = possible.lower()
        if possible_lower in column_map:
            existing_cols.append(column_map[possible_lower])
        # Also check if any column contains "lot" (case-insensitive)
        for col_name_lower, col_name_actual in column_map.items():
            if "lot" in col_name_lower and col_name_actual not in existing_cols:
                # Check if it matches our patterns
                if any(pattern.lower() in col_name_lower for pattern in possible_lot_columns):
                    existing_cols.append(col_name_actual)
    
    # Remove duplicates while preserving order
    existing_cols = list(dict.fromkeys(existing_cols))
    
    if not existing_cols:
        # Last resort: find any column with "lot" in the name
        lot_cols = [col['column_name'] for col in columns if 'lot' in col['column_name'].lower()]
        if lot_cols:
            existing_cols = lot_cols
            logger.warning(f"Using fallback lot column detection for {table_name}, found: {lot_cols}")
    
    if not existing_cols:
        raise ValueError(f"Could not find any lot column in {table_name}. Available columns: {[c['column_name'] for c in columns[:10]]}")
    
    # Check which column has the most data
    import psycopg2
    if isinstance(conn, psycopg2.extensions.connection):
        cursor = conn.cursor()
        best_col = None
        max_count = 0
        
        for col_name in existing_cols:
            try:
                # Use quoted identifier to handle case-sensitive column names
                cursor.execute(f"""
                    SELECT COUNT(*) 
                    FROM {INSPECTION_SCHEMA}.{table_name} 
                    WHERE "{col_name}" IS NOT NULL 
                    AND "{col_name}" != ''
                """)
                count = cursor.fetchone()[0]
                if count > max_count:
                    max_count = count
                    best_col = col_name
            except Exception as e:
                # Try without quotes (for lowercase column names)
                try:
                    cursor.execute(f"""
                        SELECT COUNT(*) 
                        FROM {INSPECTION_SCHEMA}.{table_name} 
                        WHERE {col_name} IS NOT NULL 
                        AND {col_name} != ''
                    """)
                    count = cursor.fetchone()[0]
                    if count > max_count:
                        max_count = count
                        best_col = col_name
                except Exception:
                    logger.debug(f"Could not check column {col_name}: {e}")
                    continue
        
        cursor.close()
        
        if best_col:
            logger.info(f"  Detected {best_col} with {max_count:,} non-null values")
            return best_col
        else:
            # Fall back to first existing column
            logger.warning(f"Could not determine best lot column, using first found: {existing_cols[0]}")
            return existing_cols[0]
    else:
        # For SQLAlchemy connections, just return first existing
        return existing_cols[0]


def create_optimized_table_sql(level: int, all_columns: Dict[int, List[Dict]]) -> str:
    """
    Generate SQL to create optimized inspection table
    
    Args:
        level: Inspection level (1-6)
        all_columns: Dict mapping level to list of column info dicts
        
    Returns:
        SQL CREATE TABLE statement
    """
    table_name = f"inspection_sheet_{level}"
    
    # Get columns from level 1 (base)
    base_columns = all_columns[1]
    
    # Build column definitions with prefixes
    column_defs = []
    
    # Add id column first (needed for lot_no fallback)
    id_col_info = next((col for col in base_columns if col['column_name'] == 'id'), None)
    if id_col_info:
        sql_type = map_postgres_type(id_col_info['data_type'], id_col_info.get('max_length'))
        column_defs.append(f'    id {sql_type}')
    
    # Add all columns from level 1 with s1_ prefix
    # Skip lot columns since we'll add s1_lot_no explicitly later
    lot_column_names = ['lot_no', 'lot_id', 'lot_number', 'mrno_lot_no']
    for col_info in base_columns:
        col_name = col_info['column_name']
        data_type = col_info['data_type']
        max_length = col_info.get('max_length')
        
        # Skip system columns that we'll add separately (id already added above)
        if col_name in ['id', 'created_at', 'updated_at']:
            continue
        
        # Skip lot columns - we'll add s1_lot_no explicitly later
        if col_name.lower() in [lc.lower() for lc in lot_column_names]:
            continue
            
        # Map PostgreSQL types to SQL
        sql_type = map_postgres_type(data_type, max_length)
        
        # Add s1_ prefix
        prefixed_name = f"s1_{col_name}"
        column_defs.append(f'    {prefixed_name} {sql_type}')
    
    # Add columns from levels 2 to level with appropriate prefixes
    for lvl in range(2, level + 1):
        if lvl in all_columns:
            for col_info in all_columns[lvl]:
                col_name = col_info['column_name']
                data_type = col_info['data_type']
                max_length = col_info.get('max_length')
                
                # Skip system columns
                if col_name in ['id', 'created_at', 'updated_at', 'inspection_level']:
                    continue
                
                # Skip if already added from level 1
                if col_name in [c['column_name'] for c in base_columns]:
                    continue
                
                sql_type = map_postgres_type(data_type, max_length)
                prefixed_name = f"s{lvl}_{col_name}"
                column_defs.append(f'    {prefixed_name} {sql_type}')
    
    # Add NULL columns for levels beyond current level
    # Skip lot columns since we'll add NULL sX_lot_no explicitly later
    for lvl in range(level + 1, MAX_LEVEL + 1):
        for col_info in base_columns:
            col_name = col_info['column_name']
            if col_name in ['id', 'created_at', 'updated_at']:
                continue
            
            # Skip lot columns - we'll add NULL sX_lot_no explicitly later
            if col_name.lower() in [lc.lower() for lc in lot_column_names]:
                continue
            
            data_type = col_info['data_type']
            max_length = col_info.get('max_length')
            sql_type = map_postgres_type(data_type, max_length)
            prefixed_name = f"s{lvl}_{col_name}"
            column_defs.append(f'    {prefixed_name} {sql_type}')
    
    # Add per-inspection lot number columns (s1_lot_no, s2_lot_no, etc.)
    # s1_lot_no is the master lot key
    for lvl in range(1, level + 1):
        column_defs.append(f'    s{lvl}_lot_no VARCHAR(100)')
    
    # Add NULL lot_no columns for levels beyond current level
    for lvl in range(level + 1, MAX_LEVEL + 1):
        column_defs.append(f'    s{lvl}_lot_no VARCHAR(100)')
    
    # Add common lot_no column (for backward compatibility and as primary key)
    # This will be populated from s1_lot_no
    column_defs.append('    lot_no VARCHAR(100)')
    
    # Add metadata columns
    column_defs.append('    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
    column_defs.append('    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
    
    # Create table SQL
    newline = '\n'
    column_list = f',{newline}'.join(column_defs)
    sql = f"""
CREATE TABLE IF NOT EXISTS {INSPECTION_SCHEMA}.{table_name} (
{column_list},
    PRIMARY KEY (lot_no)
);
"""
    return sql


def map_postgres_type(data_type: str, max_length: int = None) -> str:
    """Map PostgreSQL data type to SQL type string"""
    type_mapping = {
        'character varying': 'VARCHAR',
        'varchar': 'VARCHAR',
        'text': 'TEXT',
        'integer': 'INTEGER',
        'bigint': 'BIGINT',
        'numeric': 'NUMERIC',
        'double precision': 'DOUBLE PRECISION',
        'real': 'REAL',
        'boolean': 'BOOLEAN',
        'date': 'DATE',
        'timestamp without time zone': 'TIMESTAMP',
        'timestamp with time zone': 'TIMESTAMP WITH TIME ZONE',
        'timestamp': 'TIMESTAMP'
    }
    
    base_type = type_mapping.get(data_type.lower(), 'TEXT')
    
    if base_type == 'VARCHAR' and max_length:
        return f'VARCHAR({max_length})'
    
    return base_type


def populate_optimized_table(conn, level: int, all_columns: Dict[int, List[Dict]], 
                            lot_columns: Dict[int, str]) -> None:
    """
    Populate optimized inspection table with data from source tables
    
    Args:
        conn: Database connection
        level: Inspection level (1-6)
        all_columns: Dict mapping level to column info
        lot_columns: Dict mapping level to lot column name
    """
    table_name = f"inspection_sheet_{level}"
    base_lot_col = lot_columns[1]
    
    # Build SELECT clause - MUST match table creation order exactly
    select_parts = []
    
    # Add id column FIRST (matches table creation order)
    select_parts.append(f"i1.id AS id")
    
    # Level 1 columns with s1_ prefix
    # Exclude lot_no columns since we'll add s1_lot_no explicitly
    # CRITICAL: Process columns in the same order as table creation
    lot_column_names = ['lot_no', 'lot_id', 'lot_number', 'mrno_lot_no']
    season_col = None
    crop_col = None
    
    # First pass: identify season and crop columns
    for col_info in all_columns[1]:
        col_name = col_info['column_name'].lower()
        if col_name == 'season':
            season_col = col_info['column_name']
        elif col_name == 'crop':
            crop_col = col_info['column_name']
    
    # Second pass: add columns in table creation order
    # CRITICAL: Match table creation order exactly - all s1_ columns first, then s2_, etc.
    for col_info in all_columns[1]:
        col_name = col_info['column_name']
        if col_name in ['id', 'created_at', 'updated_at']:
            continue
        # Skip lot columns - we'll add s1_lot_no explicitly
        if col_name.lower() in [lc.lower() for lc in lot_column_names]:
            continue
        # Add all columns from level 1 (including season and crop)
        select_parts.append(f"i1.{col_name} AS s1_{col_name}")
    
    # Levels 2 to level with appropriate prefixes
    # CRITICAL: Match table creation logic exactly
    # Table creation only adds columns unique to each level (not in level 1)
    # So we should only add columns that are unique to each level
    for lvl in range(2, level + 1):
        if lvl in all_columns:
            alias = f"i{lvl}"
            level_lot_col = lot_columns.get(lvl, base_lot_col)
            
            # Only add columns unique to this level (not in level 1)
            # This matches the table creation logic
            for col_info in all_columns[lvl]:
                col_name = col_info['column_name']
                if col_name in ['id', 'created_at', 'updated_at', 'inspection_level']:
                    continue
                # Skip lot columns - we'll add sX_lot_no explicitly
                if col_name.lower() in [lc.lower() for lc in lot_column_names]:
                    continue
                # Skip if already in level 1 (table doesn't create duplicate columns)
                if col_name in [c['column_name'] for c in all_columns[1]]:
                    continue
                select_parts.append(f"{alias}.{col_name} AS s{lvl}_{col_name}")
    
    if season_col or crop_col:
        logger.info(f"Ensuring season and crop are populated from inspection_level_1 for all levels")
    
    # Add NULL columns for levels beyond current level
    # CRITICAL: Must match table creation order exactly
    # Add columns in the same order as level 1 to maintain column position
    for lvl in range(level + 1, MAX_LEVEL + 1):
        # Add NULL for all columns from level 1, in the same order
        for col_info in all_columns[1]:
            col_name = col_info['column_name']
            if col_name in ['id', 'created_at', 'updated_at']:
                continue
            # Skip lot columns - we'll add NULL sX_lot_no explicitly
            if col_name.lower() in [lc.lower() for lc in lot_column_names]:
                continue
            
            # Cast NULL to the appropriate type based on column data type
            # Use the same type mapping logic as table creation
            data_type = col_info.get('data_type', 'text').lower()
            max_length = col_info.get('max_length')
            
            # Map to SQL type using same logic as map_postgres_type
            if 'integer' in data_type or 'int' in data_type:
                null_expr = "NULL::INTEGER"
            elif 'numeric' in data_type or 'double' in data_type or 'real' in data_type:
                null_expr = "NULL::NUMERIC"
            elif 'boolean' in data_type or 'bool' in data_type:
                null_expr = "NULL::BOOLEAN"
            elif 'date' in data_type and 'timestamp' not in data_type:
                null_expr = "NULL::DATE"
            elif 'timestamp' in data_type:
                null_expr = "NULL::TIMESTAMP"
            elif 'character varying' in data_type or 'varchar' in data_type:
                if max_length:
                    null_expr = f"NULL::VARCHAR({max_length})"
                else:
                    null_expr = "NULL::VARCHAR"
            else:
                null_expr = "NULL::TEXT"
            select_parts.append(f"{null_expr} AS s{lvl}_{col_name}")
    
    # Add per-inspection lot number columns (s1_lot_no, s2_lot_no, etc.) AFTER all data columns
    # s1_lot_no is the master lot key
    for lvl in range(1, level + 1):
        if lvl == 1:
            # Add s1_lot_no explicitly (from level 1's lot column - master lot key)
            # Use COALESCE to handle NULLs, fallback to id
            # CRITICAL: Normalize lot_no using LOWER(TRIM()) to ensure consistent matching
            select_parts.append(f"LOWER(TRIM(COALESCE(CAST(i1.{base_lot_col} AS TEXT), CAST(i1.id AS TEXT)))) AS s1_lot_no")
        else:
            alias = f"i{lvl}"
            level_lot_col = lot_columns.get(lvl, base_lot_col)
            # Add sX_lot_no explicitly for this level (from the level's lot column)
            # CRITICAL: Normalize using LOWER(TRIM()) to ensure consistent matching
            # This will be NULL if the lot number doesn't match s1_lot_no (LEFT JOIN)
            select_parts.append(f"LOWER(TRIM(CAST({alias}.{level_lot_col} AS TEXT))) AS s{lvl}_lot_no")
    
    # Add NULL lot_no for levels beyond current level (always VARCHAR)
    for lvl in range(level + 1, MAX_LEVEL + 1):
        select_parts.append(f"NULL::VARCHAR(100) AS s{lvl}_lot_no")
    
    # Note: lot_no will be added separately in the INSERT SQL to ensure it's always populated
    # Don't add it here - it will be added at the end of select_list
    
    # Build FROM and JOIN clauses
    # For level 1, no JOINs needed - just select from inspection_level_1
    if level == 1:
        # Level 1: Just select all rows from inspection_level_1
        # Don't filter by lot column - include all rows
        newline = '\n'
        # Add lot_no column at the end (primary key, use same expression as s1_lot_no)
        # CRITICAL: Normalize lot_no using LOWER(TRIM()) to ensure consistent matching
        select_parts_with_lot_no = select_parts.copy()
        select_parts_with_lot_no.append(f"LOWER(TRIM(COALESCE(CAST(i1.{base_lot_col} AS TEXT), CAST(i1.id AS TEXT)))) AS lot_no")
        select_list = f',{newline}    '.join(select_parts_with_lot_no)
        # Use COALESCE to handle NULL lot columns - use id as fallback for ordering
        # CRITICAL: Normalize lot_key_expr for consistent DISTINCT ON
        lot_key_expr = f"LOWER(TRIM(COALESCE(CAST(i1.{base_lot_col} AS TEXT), CAST(i1.id AS TEXT))))"
        insert_sql = f"""
INSERT INTO {INSPECTION_SCHEMA}.{table_name}
SELECT DISTINCT ON ({lot_key_expr})
    {select_list}
FROM {INSPECTION_SCHEMA}.inspection_level_1 i1
WHERE {lot_key_expr} IS NOT NULL
ORDER BY {lot_key_expr};
"""
    else:
        # Levels 2-6: LEFT JOIN with inspection_level_1 as base
        from_clause = f"{INSPECTION_SCHEMA}.inspection_level_1 i1"
        join_clauses = []
        
        for lvl in range(2, level + 1):
            alias = f"i{lvl}"
            table_name_src = f"inspection_level_{lvl}"
            level_lot_col = lot_columns.get(lvl, base_lot_col)
            
            # Use LEFT JOIN to ensure all rows from inspection_level_1 are included
            # Compare incoming inspection lot numbers with s1_lot_no (i1.{base_lot_col})
            # Only populate sX_ columns where lot_no matches, otherwise leave NULL
            # CRITICAL: Use normalized matching - values should already be normalized, but use LOWER(TRIM()) defensively
            # Remove NULL checks from JOIN condition - LEFT JOIN will handle NULLs correctly
            join_clauses.append(
                f"LEFT JOIN {INSPECTION_SCHEMA}.{table_name_src} {alias} "
                f"ON LOWER(TRIM(CAST(i1.{base_lot_col} AS TEXT))) = LOWER(TRIM(CAST({alias}.{level_lot_col} AS TEXT)))"
            )
        
        # Build INSERT statement
        # Use DISTINCT ON to handle duplicate lot_no values (take first occurrence)
        newline = '\n'
        # Add lot_no column at the end (primary key, use same expression as s1_lot_no)
        # CRITICAL: Normalize lot_no using LOWER(TRIM()) to ensure consistent matching
        select_parts_with_lot_no = select_parts.copy()
        select_parts_with_lot_no.append(f"LOWER(TRIM(COALESCE(CAST(i1.{base_lot_col} AS TEXT), CAST(i1.id AS TEXT)))) AS lot_no")
        select_list = f',{newline}    '.join(select_parts_with_lot_no)
        join_list = ' '.join(join_clauses)
        # For levels 2-6, use LEFT JOIN and include all rows from level 1
        # Don't filter by lot column in WHERE - let LEFT JOIN handle NULLs
        # CRITICAL: Normalize lot_key_expr for consistent DISTINCT ON
        lot_key_expr = f"LOWER(TRIM(COALESCE(CAST(i1.{base_lot_col} AS TEXT), CAST(i1.id AS TEXT))))"
        insert_sql = f"""
INSERT INTO {INSPECTION_SCHEMA}.{table_name}
SELECT DISTINCT ON ({lot_key_expr})
    {select_list}
FROM {from_clause}
{join_list}
WHERE {lot_key_expr} IS NOT NULL
ORDER BY {lot_key_expr};
"""
    
    logger.info(f"Populating {table_name}...")
    logger.info(f"SQL (first 500 chars): {insert_sql[:500]}...")
    logger.debug(f"Full SQL: {insert_sql}")
    
    import psycopg2
    if isinstance(conn, psycopg2.extensions.connection):
        cursor = conn.cursor()
        try:
            # Temporarily drop primary key constraint
            cursor.execute(f"ALTER TABLE {INSPECTION_SCHEMA}.{table_name} DROP CONSTRAINT IF EXISTS {table_name}_pkey")
            
            # Temporarily drop NOT NULL constraint on lot_no
            cursor.execute(f"ALTER TABLE {INSPECTION_SCHEMA}.{table_name} ALTER COLUMN lot_no DROP NOT NULL")
            
            # Insert data
            cursor.execute(insert_sql)
            
            # Update all lot_no values using s1_lot_no
            # s1_lot_no should always have a value (COALESCE with id), so use it directly
            update_sql = f"""
            UPDATE {INSPECTION_SCHEMA}.{table_name}
            SET lot_no = s1_lot_no
            WHERE lot_no IS NULL OR lot_no = '';
            """
            cursor.execute(update_sql)
            
            # Check if any rows still have NULL lot_no and update them with sequential numbers
            cursor.execute(f"SELECT COUNT(*) FROM {INSPECTION_SCHEMA}.{table_name} WHERE lot_no IS NULL")
            null_count = cursor.fetchone()[0]
            if null_count > 0:
                # Use a CTE to generate sequential numbers for remaining NULLs
                update_sql2 = f"""
                WITH numbered_rows AS (
                    SELECT ctid, 'LOT_' || CAST(ROW_NUMBER() OVER(ORDER BY ctid) AS TEXT) AS new_lot_no
                    FROM {INSPECTION_SCHEMA}.{table_name}
                    WHERE lot_no IS NULL
                )
                UPDATE {INSPECTION_SCHEMA}.{table_name} t
                SET lot_no = nr.new_lot_no
                FROM numbered_rows nr
                WHERE t.ctid = nr.ctid;
                """
                cursor.execute(update_sql2)
            
            # Check for duplicate lot_no values and handle them
            cursor.execute(f"""
                SELECT lot_no, COUNT(*) as cnt
                FROM {INSPECTION_SCHEMA}.{table_name}
                GROUP BY lot_no
                HAVING COUNT(*) > 1
                LIMIT 10
            """)
            duplicates = cursor.fetchall()
            if duplicates:
                logger.warning(f"Found {len(duplicates)} duplicate lot_no values. Deduplicating...")
                # For duplicates, keep the first row (by ctid) and update others with unique values
                dedup_sql = f"""
                WITH ranked_rows AS (
                    SELECT ctid, lot_no, 
                           ROW_NUMBER() OVER (PARTITION BY lot_no ORDER BY ctid) as rn
                    FROM {INSPECTION_SCHEMA}.{table_name}
                ),
                duplicates_to_fix AS (
                    SELECT ctid, lot_no || '_' || CAST(rn AS TEXT) AS new_lot_no
                    FROM ranked_rows
                    WHERE rn > 1
                )
                UPDATE {INSPECTION_SCHEMA}.{table_name} t
                SET lot_no = dtf.new_lot_no
                FROM duplicates_to_fix dtf
                WHERE t.ctid = dtf.ctid;
                """
                cursor.execute(dedup_sql)
            
            # Re-add NOT NULL constraint
            cursor.execute(f"ALTER TABLE {INSPECTION_SCHEMA}.{table_name} ALTER COLUMN lot_no SET NOT NULL")
            
            # Re-add primary key constraint
            cursor.execute(f"ALTER TABLE {INSPECTION_SCHEMA}.{table_name} ADD PRIMARY KEY (lot_no)")
            
            # Log cumulative non-null counts for s2-s6
            if level >= 2:
                cursor.execute(f"""
                    SELECT 
                        COUNT(*) as total,
                        COUNT(s2_lot_no) as s2,
                        COUNT(s3_lot_no) as s3,
                        COUNT(s4_lot_no) as s4,
                        COUNT(s5_lot_no) as s5,
                        COUNT(s6_lot_no) as s6
                    FROM {INSPECTION_SCHEMA}.{table_name}
                """)
                counts = cursor.fetchone()
                logger.info("=" * 80)
                logger.info(f"CUMULATIVE NON-NULL COUNTS FOR {table_name}")
                logger.info(f"  Total rows: {counts[0]:,}")
                logger.info(f"  s2_lot_no (non-null): {counts[1]:,}")
                if level >= 3:
                    logger.info(f"  s3_lot_no (non-null): {counts[2]:,}")
                if level >= 4:
                    logger.info(f"  s4_lot_no (non-null): {counts[3]:,}")
                if level >= 5:
                    logger.info(f"  s5_lot_no (non-null): {counts[4]:,}")
                if level >= 6:
                    logger.info(f"  s6_lot_no (non-null): {counts[5]:,}")
                logger.info("=" * 80)
            
            cursor.close()
        except Exception as e:
            cursor.close()
            raise
    else:
        # For SQLAlchemy connections
        conn.execute(text(f"ALTER TABLE {INSPECTION_SCHEMA}.{table_name} DROP CONSTRAINT IF EXISTS {table_name}_pkey"))
        conn.execute(text(f"ALTER TABLE {INSPECTION_SCHEMA}.{table_name} ALTER COLUMN lot_no DROP NOT NULL"))
        conn.execute(text(insert_sql))
        update_sql = f"""
        UPDATE {INSPECTION_SCHEMA}.{table_name}
        SET lot_no = s1_lot_no
        WHERE lot_no IS NULL OR lot_no = '';
        """
        conn.execute(text(update_sql))
        
        # Check if any rows still have NULL lot_no and update them with sequential numbers
        result = conn.execute(text(f"SELECT COUNT(*) FROM {INSPECTION_SCHEMA}.{table_name} WHERE lot_no IS NULL"))
        null_count = result.scalar()
        if null_count > 0:
            # Use a CTE to generate sequential numbers for remaining NULLs
            update_sql2 = f"""
            WITH numbered_rows AS (
                SELECT ctid, 'LOT_' || CAST(ROW_NUMBER() OVER(ORDER BY ctid) AS TEXT) AS new_lot_no
                FROM {INSPECTION_SCHEMA}.{table_name}
                WHERE lot_no IS NULL
            )
            UPDATE {INSPECTION_SCHEMA}.{table_name} t
            SET lot_no = nr.new_lot_no
            FROM numbered_rows nr
            WHERE t.ctid = nr.ctid;
            """
            conn.execute(text(update_sql2))
        
        # Check for duplicate lot_no values and handle them
        result = conn.execute(text(f"""
            SELECT lot_no, COUNT(*) as cnt
            FROM {INSPECTION_SCHEMA}.{table_name}
            GROUP BY lot_no
            HAVING COUNT(*) > 1
            LIMIT 10
        """))
        duplicates = result.fetchall()
        if duplicates:
            logger.warning(f"Found {len(duplicates)} duplicate lot_no values. Deduplicating...")
            # For duplicates, keep the first row (by ctid) and update others with unique values
            dedup_sql = f"""
            WITH ranked_rows AS (
                SELECT ctid, lot_no, 
                       ROW_NUMBER() OVER (PARTITION BY lot_no ORDER BY ctid) as rn
                FROM {INSPECTION_SCHEMA}.{table_name}
            ),
            duplicates_to_fix AS (
                SELECT ctid, lot_no || '_' || CAST(rn AS TEXT) AS new_lot_no
                FROM ranked_rows
                WHERE rn > 1
            )
            UPDATE {INSPECTION_SCHEMA}.{table_name} t
            SET lot_no = dtf.new_lot_no
            FROM duplicates_to_fix dtf
            WHERE t.ctid = dtf.ctid;
            """
            conn.execute(text(dedup_sql))
        
        conn.execute(text(f"ALTER TABLE {INSPECTION_SCHEMA}.{table_name} ALTER COLUMN lot_no SET NOT NULL"))
        conn.execute(text(f"ALTER TABLE {INSPECTION_SCHEMA}.{table_name} ADD PRIMARY KEY (lot_no)"))
        
        # Log cumulative non-null counts for s2-s6
        if level >= 2:
            result = conn.execute(text(f"""
                SELECT 
                    COUNT(*) as total,
                    COUNT(s2_lot_no) as s2,
                    COUNT(s3_lot_no) as s3,
                    COUNT(s4_lot_no) as s4,
                    COUNT(s5_lot_no) as s5,
                    COUNT(s6_lot_no) as s6
                FROM {INSPECTION_SCHEMA}.{table_name}
            """))
            counts = result.fetchone()
            logger.info("=" * 80)
            logger.info(f"CUMULATIVE NON-NULL COUNTS FOR {table_name}")
            logger.info(f"  Total rows: {counts[0]:,}")
            logger.info(f"  s2_lot_no (non-null): {counts[1]:,}")
            if level >= 3:
                logger.info(f"  s3_lot_no (non-null): {counts[2]:,}")
            if level >= 4:
                logger.info(f"  s4_lot_no (non-null): {counts[3]:,}")
            if level >= 5:
                logger.info(f"  s5_lot_no (non-null): {counts[4]:,}")
            if level >= 6:
                logger.info(f"  s6_lot_no (non-null): {counts[5]:,}")
            logger.info("=" * 80)


def migrate_to_optimized_tables():
    """
    Main migration function to create and populate optimized inspection tables
    """
    logger.info("=" * 80)
    logger.info("Starting migration to optimized inspection tables")
    logger.info("=" * 80)
    
    conn = get_connection()
    try:
        # Step 1: Get all column information
        logger.info("Step 1: Gathering column information from source tables...")
        all_columns = {}
        lot_columns = {}
        
        for level in range(1, MAX_LEVEL + 1):
            table_name = f"inspection_level_{level}"
            try:
                columns = get_all_columns_from_table(conn, table_name)
                all_columns[level] = columns
                lot_col = detect_lot_column(conn, table_name)
                lot_columns[level] = lot_col
                
                # Verify the lot column has data - try both quoted and unquoted
                import psycopg2
                if isinstance(conn, psycopg2.extensions.connection):
                    cursor = conn.cursor()
                    data_count = 0
                    # Try with quotes first (for case-sensitive columns)
                    try:
                        cursor.execute(f"""
                            SELECT COUNT(*) 
                            FROM {INSPECTION_SCHEMA}.{table_name} 
                            WHERE "{lot_col}" IS NOT NULL 
                            AND "{lot_col}" != ''
                        """)
                        data_count = cursor.fetchone()[0]
                    except Exception:
                        # Try without quotes (for lowercase columns)
                        try:
                            cursor.execute(f"""
                                SELECT COUNT(*) 
                                FROM {INSPECTION_SCHEMA}.{table_name} 
                                WHERE {lot_col} IS NOT NULL 
                                AND {lot_col} != ''
                            """)
                            data_count = cursor.fetchone()[0]
                        except Exception as e:
                            logger.warning(f"  Could not verify lot column data for {table_name}: {e}")
                    
                    # Also get total row count
                    cursor.execute(f"SELECT COUNT(*) FROM {INSPECTION_SCHEMA}.{table_name}")
                    total_count = cursor.fetchone()[0]
                    cursor.close()
                    logger.info(f"  Level {level}: Found {len(columns)} columns, lot column: {lot_col} ({data_count:,} rows with data, {total_count:,} total rows)")
                    
                    # If lot column has 0 data but table has rows, try to find alternative
                    if data_count == 0 and total_count > 0:
                        logger.warning(f"  Level {level}: Lot column '{lot_col}' has no data but table has {total_count:,} rows. Checking all columns with 'lot' in name...")
                        # List all columns with 'lot' in name for debugging
                        lot_cols_found = [col['column_name'] for col in columns if 'lot' in col['column_name'].lower()]
                        logger.info(f"  All columns with 'lot' in name: {lot_cols_found}")
                        
                        # Try to find any column with 'lot' in name that has data
                        cursor = conn.cursor()
                        best_alt_col = None
                        best_alt_count = 0
                        for col_info in columns:
                            col_name = col_info['column_name']
                            if 'lot' in col_name.lower():
                                try:
                                    # Try without quotes first (for lowercase columns)
                                    try:
                                        cursor.execute(f"""
                                            SELECT COUNT(*) 
                                            FROM {INSPECTION_SCHEMA}.{table_name} 
                                            WHERE {col_name} IS NOT NULL 
                                            AND {col_name} != ''
                                        """)
                                        alt_count = cursor.fetchone()[0]
                                    except Exception:
                                        # Try with quotes (for case-sensitive columns)
                                        try:
                                            cursor.execute(f"""
                                                SELECT COUNT(*) 
                                                FROM {INSPECTION_SCHEMA}.{table_name} 
                                                WHERE "{col_name}" IS NOT NULL 
                                                AND "{col_name}" != ''
                                            """)
                                            alt_count = cursor.fetchone()[0]
                                        except Exception as e:
                                            logger.debug(f"  Could not check column {col_name}: {e}")
                                            continue
                                    
                                    logger.info(f"  Column '{col_name}': {alt_count:,} non-null rows")
                                    if alt_count > best_alt_count:
                                        best_alt_count = alt_count
                                        best_alt_col = col_name
                                except Exception as e:
                                    logger.debug(f"  Error checking column {col_name}: {e}")
                                    continue
                        
                        if best_alt_col and best_alt_count > 0:
                            logger.info(f"  ✓ Using alternative lot column '{best_alt_col}' with {best_alt_count:,} rows (was '{lot_col}')")
                            lot_columns[level] = best_alt_col
                        else:
                            logger.warning(f"  ⚠ No lot column found with data. Will proceed with '{lot_col}' and include all rows.")
                        cursor.close()
                else:
                    logger.info(f"  Level {level}: Found {len(columns)} columns, lot column: {lot_col}")
            except Exception as e:
                logger.warning(f"  Level {level}: Could not read table - {str(e)}")
                continue
        
        # Step 2: Create optimized tables
        logger.info("\nStep 2: Creating optimized tables...")
        for level in range(1, MAX_LEVEL + 1):
            table_name = f"inspection_sheet_{level}"
            try:
                # Drop table if it exists to ensure schema matches
                import psycopg2
                if isinstance(conn, psycopg2.extensions.connection):
                    cursor = conn.cursor()
                    cursor.execute(f"DROP TABLE IF EXISTS {INSPECTION_SCHEMA}.{table_name} CASCADE")
                    cursor.close()
                else:
                    conn.execute(text(f"DROP TABLE IF EXISTS {INSPECTION_SCHEMA}.{table_name} CASCADE"))
                
                create_sql = create_optimized_table_sql(level, all_columns)
                logger.info(f"  Creating {table_name}...")
                
                if isinstance(conn, psycopg2.extensions.connection):
                    cursor = conn.cursor()
                    cursor.execute(create_sql)
                    cursor.close()
                else:
                    conn.execute(text(create_sql))
                
                logger.info(f"  ✓ Created {table_name}")
            except Exception as e:
                logger.error(f"  ✗ Failed to create {table_name}: {str(e)}")
                raise
        
        # Step 3: Truncate existing optimized tables before repopulating
        logger.info("\nStep 3: Truncating existing optimized tables...")
        for level in range(1, MAX_LEVEL + 1):
            table_name = f"inspection_sheet_{level}"
            try:
                import psycopg2
                if isinstance(conn, psycopg2.extensions.connection):
                    cursor = conn.cursor()
                    cursor.execute(f"TRUNCATE TABLE {INSPECTION_SCHEMA}.{table_name} CASCADE")
                    cursor.close()
                    logger.info(f"  ✓ Truncated {table_name}")
                else:
                    conn.execute(text(f"TRUNCATE TABLE {INSPECTION_SCHEMA}.{table_name} CASCADE"))
                    logger.info(f"  ✓ Truncated {table_name}")
            except Exception as e:
                logger.warning(f"  Could not truncate {table_name}: {e}")
        
        # Step 4: Populate tables
        logger.info("\nStep 4: Populating optimized tables...")
        for level in range(1, MAX_LEVEL + 1):
            try:
                populate_optimized_table(conn, level, all_columns, lot_columns)
                
                # Verify population
                table_name = f"inspection_sheet_{level}"
                import psycopg2
                if isinstance(conn, psycopg2.extensions.connection):
                    cursor = conn.cursor()
                    cursor.execute(f"SELECT COUNT(*) FROM {INSPECTION_SCHEMA}.{table_name}")
                    row_count = cursor.fetchone()[0]
                    cursor.close()
                    logger.info(f"  ✓ Populated inspection_sheet_{level} ({row_count:,} rows)")
                else:
                    result = conn.execute(text(f"SELECT COUNT(*) FROM {INSPECTION_SCHEMA}.{table_name}"))
                    row_count = result.scalar()
                    logger.info(f"  ✓ Populated inspection_sheet_{level} ({row_count:,} rows)")
            except Exception as e:
                logger.error(f"  ✗ Failed to populate inspection_sheet_{level}: {str(e)}")
                raise
        
        # Commit transaction
        if isinstance(conn, psycopg2.extensions.connection):
            conn.commit()
        
        logger.info("\n" + "=" * 80)
        logger.info("Migration completed successfully!")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        if isinstance(conn, psycopg2.extensions.connection):
            conn.rollback()
        raise
    finally:
        release_connection(conn)


if __name__ == "__main__":
    migrate_to_optimized_tables()

