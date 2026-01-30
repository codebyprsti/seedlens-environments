"""
Dynamic SQL query builder for cumulative inspection data
Builds JOIN queries dynamically based on inspection level
"""
from sqlalchemy import text, Table, MetaData, inspect
from typing import List, Dict, Any, Optional
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Schema where inspection tables are stored
INSPECTION_SCHEMA = "operations"
# Base table name pattern
TABLE_PREFIX = "inspection_level_"
# Possible column names used for joining (will try these in order)
# IMPORTANT: lot_no is the primary key for comparisons, prefer it over lot_id
# Note: mrno_lot_no and lot_no are treated as equivalent
POSSIBLE_LOT_COLUMNS = ["lot_no", "mrno_lot_no", "lot_number", "lot_id"]

def are_lot_columns_equivalent(col1: str, col2: str) -> bool:
    """
    Check if two lot column names are equivalent for joining purposes
    mrno_lot_no and lot_no are considered equivalent
    
    Args:
        col1: First column name
        col2: Second column name
        
    Returns:
        True if columns are equivalent, False otherwise
    """
    # Normalize column names
    normalized_cols = {
        "lot_no": ["lot_no", "mrno_lot_no"],
        "mrno_lot_no": ["lot_no", "mrno_lot_no"],
        "lot_id": ["lot_id"],
        "lot_number": ["lot_number"]
    }
    
    # Get normalized sets for both columns
    set1 = normalized_cols.get(col1, [col1])
    set2 = normalized_cols.get(col2, [col2])
    
    # Check if they share any common normalized value
    return bool(set(set1) & set(set2))
# Maximum inspection level
MAX_LEVEL = 6

# Cache for lot column names (cleared on application restart)
_lot_column_cache: Dict[int, str] = {}


def validate_level(level: int) -> None:
    """
    Validate inspection level
    
    Args:
        level: Inspection level (1-6)
        
    Raises:
        ValueError: If level is not between 1 and 6
    """
    if not isinstance(level, int) or level < 1 or level > MAX_LEVEL:
        raise ValueError(f"Inspection level must be between 1 and {MAX_LEVEL}, got {level}")


def get_table_name(level: int) -> str:
    """
    Get table name for a given inspection level
    
    Args:
        level: Inspection level (1-6)
        
    Returns:
        Table name (e.g., 'inspection_level_1')
    """
    validate_level(level)
    return f"{TABLE_PREFIX}{level}"


def check_optimized_table_exists(conn, level: int) -> bool:
    """
    Check if optimized inspection table exists
    
    Args:
        conn: Database connection
        level: Inspection level (1-6)
        
    Returns:
        True if table exists, False otherwise
    """
    table_name = f"inspection_sheet_{level}"
    
    import psycopg2
    if isinstance(conn, psycopg2.extensions.connection):
        cursor = conn.cursor()
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = %s 
                AND table_name = %s
            )
        """, (INSPECTION_SCHEMA, table_name))
        exists = cursor.fetchone()[0]
        cursor.close()
        return exists
    else:
        query = text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = :schema 
                AND table_name = :table_name
            )
        """)
        result = conn.execute(query, {"schema": INSPECTION_SCHEMA, "table_name": table_name})
        return result.scalar()


def build_optimized_cumulative_query(level: int, limit: int = None, offset: int = 0, conn=None) -> str:
    """
    Build optimized SQL query using pre-populated inspection_sheet tables.
    This is much faster than JOINs as data is already pre-joined.
    
    Args:
        level: Inspection level (1-6)
        limit: Maximum number of rows to return (None for no limit)
        offset: Number of rows to skip (default: 0)
        conn: Database connection (optional, for checking table existence)
        
    Returns:
        SQL query string
        
    Raises:
        ValueError: If optimized table doesn't exist (when conn is provided)
    """
    validate_level(level)
    
    # Validate pagination parameters
    if limit is not None and limit < 1:
        raise ValueError("Limit must be a positive integer")
    if offset < 0:
        raise ValueError("Offset must be a non-negative integer")
    
    # Check if optimized table exists (if connection provided)
    if conn:
        if not check_optimized_table_exists(conn, level):
            raise ValueError(f"Optimized table inspection_sheet_{level} does not exist. Run migration first.")
    
    # Query directly from the optimized table (no JOINs needed!)
    table_name = f"inspection_sheet_{level}"
    
    query = f"""
SELECT *
FROM {INSPECTION_SCHEMA}.{table_name}
WHERE lot_no IS NOT NULL
"""
    
    # Add pagination
    if limit is not None:
        query += f"\nLIMIT {limit}"
    if offset > 0:
        query += f"\nOFFSET {offset}"
    
    logger.info(f"Generated optimized query for level {level} from {table_name}, limit={limit}, offset={offset}")
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"Full SQL query:\n{query}")
    
    return query.strip()


def build_cumulative_query(level: int, lot_columns: Dict[int, str] = None, limit: int = None, offset: int = 0, conn=None, use_optimized: bool = True) -> str:
    """
    Build dynamic SQL query for cumulative inspection data
    
    The query joins inspection_level_1 with subsequent levels up to the specified level.
    Each level is joined directly to inspection_level_1 (not to previous levels).
    
    Args:
        level: Inspection level (1-6)
        lot_columns: Dictionary mapping level to column name (e.g., {1: 'lot_id', 2: 'lot_id', 3: 'lot_no'})
                    If None, will use first from POSSIBLE_LOT_COLUMNS for all
        limit: Maximum number of rows to return (None for no limit)
        offset: Number of rows to skip (default: 0)
        conn: Database connection (optional, for column detection)
        use_optimized: If True, use optimized inspection_sheet tables (default: True)
        
    Returns:
        SQL query string
        
    Example:
        For level 3 (optimized):
        SELECT * FROM operations.inspection_sheet_3 WHERE lot_no IS NOT NULL LIMIT 100 OFFSET 0
        
        For level 3 (legacy with JOINs):
        SELECT * FROM operations.inspection_level_1 i1
        INNER JOIN operations.inspection_level_2 i2 ON i1.lot_id = i2.lot_id
        INNER JOIN operations.inspection_level_3 i3 ON i1.lot_id = i3.lot_no
        LIMIT 100 OFFSET 0
    """
    validate_level(level)
    
    # Use optimized tables if available
    if use_optimized and conn:
        try:
            if check_optimized_table_exists(conn, level):
                return build_optimized_cumulative_query(level, limit, offset, conn)
            else:
                logger.info(f"Optimized table inspection_sheet_{level} not found, using JOIN-based query")
        except Exception as e:
            logger.warning(f"Failed to use optimized query, falling back to JOIN-based query: {str(e)}")
            # Fall through to legacy implementation
    
    # Validate pagination parameters
    if limit is not None and limit < 1:
        raise ValueError("Limit must be a positive integer")
    if offset < 0:
        raise ValueError("Offset must be a non-negative integer")
    
    # Use provided lot_columns or default to first possible column for all
    if lot_columns is None:
        lot_columns = {1: POSSIBLE_LOT_COLUMNS[0]}
        for lvl in range(2, level + 1):
            lot_columns[lvl] = POSSIBLE_LOT_COLUMNS[0]
    
    # Ensure level 1 has a column name
    if 1 not in lot_columns:
        lot_columns[1] = POSSIBLE_LOT_COLUMNS[0]
    
    base_lot_column = lot_columns.get(1, POSSIBLE_LOT_COLUMNS[0])
    
    if level == 1:
        # Level 1: Only base table - prefix all columns with s1_
        if conn:
            # Get column names and prefix them explicitly
            columns = get_table_columns(conn, 1)
            select_parts = [f"i1.{col} as s1_{col}" for col in columns]
            query = f"""
        SELECT 
            {', '.join(select_parts)}
        FROM {INSPECTION_SCHEMA}.{get_table_name(1)} i1
        WHERE i1.{base_lot_column} IS NOT NULL
        """
        else:
            # Fallback: use SELECT * (will be prefixed in post-processing)
            query = f"""
        SELECT 
            i1.*
        FROM {INSPECTION_SCHEMA}.{get_table_name(1)} i1
        WHERE i1.{base_lot_column} IS NOT NULL
        """
    else:
        # Level 2+: Join all levels up to the specified level
        # Each level is joined to inspection_level_1
        from_parts = [f"{INSPECTION_SCHEMA}.{get_table_name(1)} i1"]
        join_parts = []
        select_parts = []
        
        # Get column names for each table and prefix them explicitly
        if conn:
            # Level 1 columns with s1_ prefix
            cols_1 = get_table_columns(conn, 1)
            select_parts.extend([f"i1.{col} as s1_{col}" for col in cols_1])
            
            # Build JOIN clauses and get columns for levels 2 through level
            for lvl in range(2, level + 1):
                alias = f"i{lvl}"
                sheet_prefix = f"s{lvl}"
                table_name = get_table_name(lvl)
                level_lot_column = lot_columns.get(lvl, base_lot_column)
                
                # Build JOIN
                # Handle different column names (e.g., lot_no vs mrno_lot_no)
                # Use casting and trimming to ensure proper matching regardless of data type differences
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Joining level 1 (column: {base_lot_column}) with level {lvl} (column: {level_lot_column})")
                
                # Always use TRIM and CAST to handle potential type mismatches and whitespace
                # This ensures lot_no and mrno_lot_no match correctly even if they have different types
                join_parts.append(
                    f"INNER JOIN {INSPECTION_SCHEMA}.{table_name} {alias} "
                    f"ON TRIM(CAST(i1.{base_lot_column} AS TEXT)) = TRIM(CAST({alias}.{level_lot_column} AS TEXT)) "
                    f"AND i1.{base_lot_column} IS NOT NULL "
                    f"AND {alias}.{level_lot_column} IS NOT NULL"
                )
                
                # Get columns for this level and prefix them with s2_, s3_, etc.
                cols_lvl = get_table_columns(conn, lvl)
                select_parts.extend([f"{alias}.{col} as {sheet_prefix}_{col}" for col in cols_lvl])
        else:
            # Fallback: use SELECT * (will be prefixed in post-processing)
            select_parts.append("i1.*")
            for lvl in range(2, level + 1):
                alias = f"i{lvl}"
                table_name = get_table_name(lvl)
                level_lot_column = lot_columns.get(lvl, base_lot_column)
                
                if base_lot_column == level_lot_column:
                    join_parts.append(
                        f"INNER JOIN {INSPECTION_SCHEMA}.{table_name} {alias} "
                        f"ON i1.{base_lot_column} = {alias}.{level_lot_column} "
                        f"AND i1.{base_lot_column} IS NOT NULL "
                        f"AND {alias}.{level_lot_column} IS NOT NULL"
                    )
                else:
                    join_parts.append(
                        f"INNER JOIN {INSPECTION_SCHEMA}.{table_name} {alias} "
                        f"ON TRIM(CAST(i1.{base_lot_column} AS TEXT)) = TRIM(CAST({alias}.{level_lot_column} AS TEXT)) "
                        f"AND i1.{base_lot_column} IS NOT NULL "
                        f"AND {alias}.{level_lot_column} IS NOT NULL"
                    )
                select_parts.append(f"{alias}.*")
        
        # Build SELECT with all columns from all tables
        query = f"""
        SELECT 
            {', '.join(select_parts)}
        FROM {from_parts[0]}
        {' '.join(join_parts)}
        WHERE i1.{base_lot_column} IS NOT NULL
        """
    
    # Add pagination
    if limit is not None:
        query += f"\nLIMIT {limit}"
    if offset > 0:
        query += f"\nOFFSET {offset}"
    
    # Always log the query for troubleshooting
    logger.info(f"Generated query for level {level} with lot_columns={lot_columns}, limit={limit}, offset={offset}")
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"Full SQL query:\n{query}")
    return query.strip()


def build_cumulative_query_with_column_prefixes(level: int, lot_column: str = None) -> str:
    """
    Build dynamic SQL query with column prefixes to avoid conflicts
    
    This version prefixes all columns with their table alias to avoid
    column name conflicts when multiple tables have the same column names.
    
    Args:
        level: Inspection level (1-6)
        lot_column: Column name for lot number (if None, will use first from POSSIBLE_LOT_COLUMNS)
        
    Returns:
        SQL query string with prefixed columns
    """
    validate_level(level)
    
    # Use provided lot_column or default to first possible column
    if lot_column is None:
        lot_column = POSSIBLE_LOT_COLUMNS[0]
    
    if level == 1:
        query = f"""
        SELECT i1.*
        FROM {INSPECTION_SCHEMA}.{get_table_name(1)} i1
        """
    else:
        select_parts = []
        from_parts = [f"{INSPECTION_SCHEMA}.{get_table_name(1)} i1"]
        join_parts = []
        
        # Get column names for each table and prefix them
        # For now, we'll use a simpler approach: select all columns with aliases
        # In production, you might want to explicitly list columns
        
        # For level 1, select all columns
        select_parts.append("i1.*")
        
        # For levels 2 through level, join and select
        for lvl in range(2, level + 1):
            alias = f"i{lvl}"
            table_name = get_table_name(lvl)
            join_parts.append(
                f"INNER JOIN {INSPECTION_SCHEMA}.{table_name} {alias} "
                f"ON i1.{lot_column} = {alias}.{lot_column}"
            )
            select_parts.append(f"{alias}.*")
        
        query = f"""
        SELECT {', '.join(select_parts)}
        FROM {from_parts[0]}
        {' '.join(join_parts)}
        """
    
    logger.debug(f"Generated query with prefixes for level {level}:\n{query}")
    return query.strip()


def get_table_columns(conn, level: int) -> List[str]:
    """
    Get column names for a given inspection level table
    
    Args:
        conn: Database connection (psycopg2 or SQLAlchemy)
        level: Inspection level (1-6)
        
    Returns:
        List of column names
    """
    validate_level(level)
    table_name = get_table_name(level)
    
    # Check if it's a psycopg2 connection or SQLAlchemy connection
    # psycopg2 connections are instances of psycopg2.extensions.connection
    # SQLAlchemy connections have 'execute' method that accepts text() objects
    import psycopg2
    if isinstance(conn, psycopg2.extensions.connection):
        # psycopg2 connection
        cursor = conn.cursor()
        cursor.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s
            AND table_name = %s
            ORDER BY ordinal_position
        """, (INSPECTION_SCHEMA, table_name))
        columns = [row[0] for row in cursor.fetchall()]
        cursor.close()
        return columns
    else:
        # SQLAlchemy connection (has execute method)
        query = text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema
            AND table_name = :table_name
            ORDER BY ordinal_position
        """)
        result = conn.execute(query, {"schema": INSPECTION_SCHEMA, "table_name": table_name})
        return [row[0] for row in result]


def detect_lot_column_name(conn, level: int = 1, use_cache: bool = True) -> str:
    """
    Detect the actual lot number column name from the inspection table
    Uses caching to avoid repeated information_schema queries
    
    Args:
        conn: Database connection
        level: Inspection level to check (default: 1)
        use_cache: Whether to use cached values (default: True)
        
    Returns:
        Column name for lot number
        
    Raises:
        ValueError: If no lot column is found
    """
    validate_level(level)
    
    # Check cache first
    if use_cache and level in _lot_column_cache:
        logger.debug(f"Using cached lot column '{_lot_column_cache[level]}' for level {level}")
        return _lot_column_cache[level]
    
    columns = get_table_columns(conn, level)
    
    logger.debug(f"Columns in {INSPECTION_SCHEMA}.{get_table_name(level)}: {columns}")
    
    # Try to find a matching column
    for possible_name in POSSIBLE_LOT_COLUMNS:
        if possible_name in columns:
            logger.info(f"Found lot column '{possible_name}' in inspection_level_{level}")
            # Cache the result
            if use_cache:
                _lot_column_cache[level] = possible_name
            return possible_name
    
    # If not found, raise error with detailed information
    error_msg = (
        f"Could not find lot number column in {INSPECTION_SCHEMA}.{get_table_name(level)}. "
        f"Available columns: {columns}. Tried: {POSSIBLE_LOT_COLUMNS}"
    )
    logger.error(error_msg)
    raise ValueError(error_msg)


def clear_lot_column_cache():
    """Clear the lot column name cache (useful for testing or schema changes)"""
    global _lot_column_cache
    _lot_column_cache.clear()
    logger.info("Lot column cache cleared")

