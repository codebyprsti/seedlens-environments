"""
Business logic for cumulative inspection data
"""
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text
from core.db import get_connection, release_connection
import logging

from .queries import (
    build_cumulative_query,
    validate_level,
    MAX_LEVEL,
    detect_lot_column_name,
    get_table_columns
)

logger = logging.getLogger(__name__)


class CumulativeInspectionService:
    """Service for handling cumulative inspection data queries"""
    
    def __init__(self, db: Session = None, enable_diagnostics: bool = False):
        """
        Initialize the service
        
        Args:
            db: SQLAlchemy session (optional, will use connection pool if not provided)
            enable_diagnostics: Enable diagnostic queries (default: False, only runs in DEBUG mode)
        """
        self.db = db
        self.use_connection_pool = db is None
        self.enable_diagnostics = enable_diagnostics
    
    def get_cumulative_inspection_data(self, level: int, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """
        Get cumulative inspection data for a given level
        
        Args:
            level: Inspection level (1-6)
            limit: Maximum number of rows to return (default: 100, max: 1000)
            offset: Number of rows to skip for pagination (default: 0)
            
        Returns:
            Dictionary with:
                - inspection_level: int
                - total_lots: int (number of rows returned, not total available)
                - limit: int
                - offset: int
                - has_more: bool (indicates if more data is available)
                - data: List[Dict] - list of row dictionaries
                
        Raises:
            ValueError: If level is invalid or pagination parameters are invalid
            Exception: If database query fails
        """
        validate_level(level)
        
        # Validate and enforce limits
        if limit is None:
            limit = 100  # Default limit
        if limit < 1:
            raise ValueError("Limit must be a positive integer")
        if limit > 1000:
            limit = 1000  # Enforce maximum limit for production safety
            logger.warning(f"Limit exceeded maximum (1000), using 1000 instead")
        if offset < 0:
            raise ValueError("Offset must be a non-negative integer")
        
        try:
            # Detect the actual lot column name for each inspection level (with caching)
            # Also track actual column names (may be mrno_lot_no) vs normalized names (lot_no)
            lot_columns = {}  # Normalized names for joining logic
            actual_lot_columns = {}  # Actual column names in database
            
            # Use a single connection for all column detections
            if self.use_connection_pool:
                conn = get_connection()
                try:
                    # Detect for level 1 (base) - cached after first call
                    lot_columns[1], actual_lot_columns[1] = self._detect_lot_column_with_actual(conn, level=1)
                    logger.info(f"Level 1: detected lot column '{actual_lot_columns[1]}' (normalized: '{lot_columns[1]}')")
                    
                    # Detect for all other levels that will be joined - also cached
                    for lvl in range(2, level + 1):
                        try:
                            lot_columns[lvl], actual_lot_columns[lvl] = self._detect_lot_column_with_actual(conn, level=lvl)
                            logger.info(f"Level {lvl}: detected lot column '{actual_lot_columns[lvl]}' (normalized: '{lot_columns[lvl]}')")
                        except ValueError as e:
                            # If a level doesn't have a lot column, use level 1's column
                            logger.warning(f"Could not detect lot column for level {lvl}, using level 1's column ({lot_columns[1]}): {e}")
                            lot_columns[lvl] = lot_columns[1]
                            actual_lot_columns[lvl] = actual_lot_columns[1]
                finally:
                    release_connection(conn)
            else:
                # For SQLAlchemy session, get raw connection from bind
                raw_conn = self.db.bind.connect()
                try:
                    # Detect for level 1 (base) - cached after first call
                    lot_columns[1], actual_lot_columns[1] = self._detect_lot_column_with_actual(raw_conn, level=1)
                    logger.info(f"Level 1: detected lot column '{actual_lot_columns[1]}' (normalized: '{lot_columns[1]}')")
                    
                    # Detect for all other levels that will be joined - also cached
                    for lvl in range(2, level + 1):
                        try:
                            lot_columns[lvl], actual_lot_columns[lvl] = self._detect_lot_column_with_actual(raw_conn, level=lvl)
                            logger.info(f"Level {lvl}: detected lot column '{actual_lot_columns[lvl]}' (normalized: '{lot_columns[lvl]}')")
                        except ValueError as e:
                            # If a level doesn't have a lot column, use level 1's column
                            logger.warning(f"Could not detect lot column for level {lvl}, using level 1's column ({lot_columns[1]}): {e}")
                            lot_columns[lvl] = lot_columns[1]
                            actual_lot_columns[lvl] = actual_lot_columns[1]
                finally:
                    raw_conn.close()
            
            # Build the dynamic query - use optimized tables if available
            # Get connection to check for optimized tables and build query
            if self.use_connection_pool:
                query_conn = get_connection()
                try:
                    query_str = build_cumulative_query(
                        level, 
                        lot_columns=actual_lot_columns,  # Use actual column names (for fallback)
                        limit=limit, 
                        offset=offset,
                        conn=query_conn,  # Needed to check if optimized tables exist
                        use_optimized=True  # Use optimized tables by default
                    )
                finally:
                    release_connection(query_conn)
            else:
                query_conn = self.db.bind.connect()
                try:
                    query_str = build_cumulative_query(
                        level, 
                        lot_columns=actual_lot_columns,  # Use actual column names (for fallback)
                        limit=limit, 
                        offset=offset,
                        conn=query_conn,  # Needed to check if optimized tables exist
                        use_optimized=True  # Use optimized tables by default
                    )
                finally:
                    query_conn.close()
            
            query = text(query_str)
            
            # Log the query and column mappings for debugging
            logger.info(f"Executing query for level {level}")
            logger.info(f"Actual lot columns detected: {actual_lot_columns}")
            logger.info(f"Normalized lot columns: {lot_columns}")
            # Always log the full query for troubleshooting
            logger.info(f"Generated SQL query:\n{query_str}")
            
            # Execute query
            if self.use_connection_pool:
                # Use connection pool for raw SQL with psycopg2
                import psycopg2.extras
                conn = get_connection()
                try:
                    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                    cursor.execute(query_str)
                    rows = cursor.fetchall()
                    # Convert RealDictRow to dict and handle datetime serialization
                    data = []
                    for row in rows:
                        row_dict = {}
                        for key, value in row.items():
                            if value is None:
                                row_dict[key] = None
                            elif hasattr(value, 'isoformat'):  # datetime objects
                                row_dict[key] = value.isoformat()
                            else:
                                row_dict[key] = value
                        data.append(row_dict)
                    cursor.close()
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(f"Query executed successfully. Found {len(data)} rows")
                finally:
                    release_connection(conn)
            else:
                # Use SQLAlchemy session
                result = self.db.execute(query)
                rows = result.fetchall()
                columns = result.keys()
                
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Query executed successfully. Found {len(rows)} rows")
                
                # Convert rows to dictionaries
                # Columns are already prefixed in SQL query (s1_col, s2_col, etc.)
                data = []
                for row in rows:
                    row_dict = {}
                    for i, col in enumerate(columns):
                        value = row[i]
                        # Column name is already prefixed from SQL query
                        # Handle None values and convert to JSON-serializable types
                        if value is None:
                            row_dict[col] = None
                        elif hasattr(value, 'isoformat'):  # datetime objects
                            row_dict[col] = value.isoformat()
                        else:
                            row_dict[col] = value
                    data.append(row_dict)
            
            # If no data found, log diagnostic information
            if len(data) == 0:
                logger.warning(f"No data found for level {level}. Running diagnostics...")
                try:
                    # Use actual column names for diagnostics
                    self._log_diagnostic_info(level, actual_lot_columns)
                except Exception as diag_error:
                    logger.error(f"Error running diagnostics: {str(diag_error)}", exc_info=True)
            
            # Determine if there's more data available
            # If we got exactly the limit, there might be more
            has_more = len(data) == limit
            
            return {
                "inspection_level": level,
                "total_lots": len(data),
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "data": data
            }
            
        except ValueError as e:
            logger.error(f"Validation error for level {level}: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Error fetching cumulative inspection data for level {level}: {str(e)}")
            raise Exception(f"Failed to fetch cumulative inspection data: {str(e)}")
    
    def _detect_lot_column_with_actual(self, conn, level: int) -> tuple:
        """
        Detect lot column name and return both normalized and actual names
        
        Args:
            conn: Database connection
            level: Inspection level
            
        Returns:
            Tuple of (normalized_name, actual_name)
            normalized_name: 'lot_no' (for joining logic)
            actual_name: actual column name in DB (may be 'mrno_lot_no' or 'lot_no')
        """
        from .queries import get_table_columns, POSSIBLE_LOT_COLUMNS
        
        columns = get_table_columns(conn, level)
        
        # Try to find a matching column
        for possible_name in POSSIBLE_LOT_COLUMNS:
            if possible_name in columns:
                # Normalize: treat mrno_lot_no as lot_no for consistency in joining
                normalized = "lot_no" if possible_name in ["mrno_lot_no", "lot_no"] else possible_name
                return (normalized, possible_name)  # Return (normalized, actual)
        
        # If not found, raise error
        raise ValueError(
            f"Could not find lot number column in inspection_level_{level}. "
            f"Available columns: {columns}. Tried: {POSSIBLE_LOT_COLUMNS}"
        )
    
    def validate_inspection_level(self, level: int) -> bool:
        """
        Validate that inspection level is within acceptable range
        
        Args:
            level: Inspection level to validate
            
        Returns:
            True if valid, False otherwise
        """
        try:
            validate_level(level)
            return True
        except ValueError:
            return False
    
    def _log_diagnostic_info(self, level: int, lot_columns: Dict[int, str]):
        """
        Log diagnostic information to help debug empty results
        
        Args:
            level: Inspection level
            lot_columns: Dictionary mapping level to column name
        """
        try:
            if self.use_connection_pool:
                conn = get_connection()
                try:
                    self._run_diagnostics(conn, level, lot_columns)
                finally:
                    release_connection(conn)
            else:
                raw_conn = self.db.bind.connect()
                try:
                    self._run_diagnostics(raw_conn, level, lot_columns)
                finally:
                    raw_conn.close()
        except Exception as e:
            logger.error(f"Error running diagnostics: {str(e)}")
    
    def _run_diagnostics(self, conn, level: int, lot_columns: Dict[int, str]):
        """Run diagnostic queries to understand why no data is returned"""
        import psycopg2
        
        if isinstance(conn, psycopg2.extensions.connection):
            cursor = conn.cursor()
            try:
                # Check row counts
                for lvl in range(1, level + 1):
                    table_name = f"inspection_level_{lvl}"
                    lot_col = lot_columns.get(lvl, "lot_no")
                    cursor.execute(f"""
                        SELECT COUNT(*) as total_rows,
                               COUNT({lot_col}) as non_null_lots,
                               COUNT(DISTINCT {lot_col}) as unique_lots
                        FROM operations.{table_name}
                    """)
                    stats = cursor.fetchone()
                    logger.info(f"Level {lvl} ({table_name}): Total rows={stats[0]}, Non-null {lot_col}={stats[1]}, Unique {lot_col}={stats[2]}")
                
                # Check for common lot numbers between level 1 and other levels
                base_lot_col = lot_columns.get(1, "lot_no")
                for lvl in range(2, level + 1):
                    table_name = f"inspection_level_{lvl}"
                    level_lot_col = lot_columns.get(lvl, base_lot_col)
                    # Use same casting/trimming logic as main query to handle lot_no vs mrno_lot_no
                    cursor.execute(f"""
                        SELECT COUNT(*) as matching_lots
                        FROM operations.inspection_level_1 i1
                        INNER JOIN operations.{table_name} i{lvl}
                        ON TRIM(CAST(i1.{base_lot_col} AS TEXT)) = TRIM(CAST(i{lvl}.{level_lot_col} AS TEXT))
                        AND i1.{base_lot_col} IS NOT NULL
                        AND i{lvl}.{level_lot_col} IS NOT NULL
                    """)
                    match_count = cursor.fetchone()[0]
                    logger.info(f"Matching lots between level 1 and level {lvl}: {match_count}")
                    
                    # Show sample lot numbers from each table for comparison
                    cursor.execute(f"""
                        SELECT DISTINCT {base_lot_col} as lot_value
                        FROM operations.inspection_level_1
                        WHERE {base_lot_col} IS NOT NULL
                        LIMIT 5
                    """)
                    sample_lots_1 = [row[0] for row in cursor.fetchall()]
                    logger.info(f"Sample lot numbers from level 1: {sample_lots_1}")
                    
                    cursor.execute(f"""
                        SELECT DISTINCT {level_lot_col} as lot_value
                        FROM operations.{table_name}
                        WHERE {level_lot_col} IS NOT NULL
                        LIMIT 5
                    """)
                    sample_lots_lvl = [row[0] for row in cursor.fetchall()]
                    logger.info(f"Sample lot numbers from level {lvl}: {sample_lots_lvl}")
            finally:
                cursor.close()
        else:
            # SQLAlchemy connection
            from sqlalchemy import text
            for lvl in range(1, level + 1):
                table_name = f"inspection_level_{lvl}"
                lot_col = lot_columns.get(lvl, "lot_no")
                query = text(f"""
                    SELECT COUNT(*) as total_rows,
                           COUNT({lot_col}) as non_null_lots,
                           COUNT(DISTINCT {lot_col}) as unique_lots
                    FROM operations.{table_name}
                """)
                result = conn.execute(query)
                stats = result.fetchone()
                logger.info(f"Level {lvl} ({table_name}): Total rows={stats[0]}, Non-null {lot_col}={stats[1]}, Unique {lot_col}={stats[2]}")
            
            # Check for common lot numbers
            base_lot_col = lot_columns.get(1, "lot_no")
            for lvl in range(2, level + 1):
                table_name = f"inspection_level_{lvl}"
                level_lot_col = lot_columns.get(lvl, base_lot_col)
                # Use same casting logic as main query
                query = text(f"""
                    SELECT COUNT(*) as matching_lots
                    FROM operations.inspection_level_1 i1
                    INNER JOIN operations.{table_name} i{lvl}
                    ON TRIM(CAST(i1.{base_lot_col} AS TEXT)) = TRIM(CAST(i{lvl}.{level_lot_col} AS TEXT))
                    WHERE i1.{base_lot_col} IS NOT NULL AND i{lvl}.{level_lot_col} IS NOT NULL
                """)
                result = conn.execute(query)
                match_count = result.fetchone()[0]
                logger.info(f"Matching lots between level 1 and level {lvl}: {match_count}")
                
                # Show sample lot numbers from each table for comparison
                query = text(f"""
                    SELECT DISTINCT {base_lot_col} as lot_value
                    FROM operations.inspection_level_1
                    WHERE {base_lot_col} IS NOT NULL
                    LIMIT 5
                """)
                result = conn.execute(query)
                sample_lots_1 = [row[0] for row in result]
                logger.info(f"Sample lot numbers from level 1: {sample_lots_1}")
                
                query = text(f"""
                    SELECT DISTINCT {level_lot_col} as lot_value
                    FROM operations.{table_name}
                    WHERE {level_lot_col} IS NOT NULL
                    LIMIT 5
                """)
                result = conn.execute(query)
                sample_lots_lvl = [row[0] for row in result]
                logger.info(f"Sample lot numbers from level {lvl}: {sample_lots_lvl}")

