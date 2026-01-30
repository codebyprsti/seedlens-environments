import logging
import psycopg2
import psycopg2.extras
from typing import List, Optional, Dict, Any
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from models.db_models import VarietyRecord
from models.schemas.base import VarietyUpdateRequest, VarietyResponse
from core.db import get_connection, release_connection

logger = logging.getLogger(__name__)


class VarietyService:
    """Service for managing variety records."""

    def __init__(self, db: Session):
        self.db = db

    def get_all_varieties(
        self,
        variety_name: Optional[str] = None,
        crop_id: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[VarietyResponse]:
        """
        Fetch all varieties from operations.varieties table with optional filtering.
        
        Args:
            variety_name: Optional filter for variety name (ILIKE search)
            crop_id: Optional filter for crop ID
            limit: Maximum number of records to return (max 5000)
            offset: Number of records to skip
            
        Returns:
            List of VarietyResponse objects
        """
        try:
            # Cap the limit to prevent performance issues
            if limit > 5000:
                limit = 5000
                logger.warning(f"Limit capped at 5000 for performance")
            
            logger.info(f"Fetching varieties with limit={limit}, offset={offset}")
            
            # Build WHERE conditions
            where_conditions = []
            params = {}
            
            if variety_name:
                where_conditions.append("variety_name ILIKE :variety_name")
                params['variety_name'] = f"%{variety_name}%"
            
            if crop_id:
                where_conditions.append("crop_id = :crop_id")
                params['crop_id'] = crop_id
            
            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
            
            # Use raw SQL for better performance and control
            sql_query = text(f"""
                SELECT 
                    variety_id,
                    variety_name,
                    crop_id,
                    category_id,
                    created_at,
                    updated_at
                FROM operations.varieties
                WHERE {where_clause}
                ORDER BY variety_id
                LIMIT :limit OFFSET :offset
            """)
            
            params['limit'] = limit
            params['offset'] = offset
            
            logger.info("Executing query...")
            result_set = self.db.execute(sql_query, params)
            varieties = result_set.fetchall()
            logger.info(f"Query returned {len(varieties)} records")
            
            # Convert to response objects
            result = []
            for idx, row in enumerate(varieties):
                try:
                    variety_dict = {
                        'variety_id': row.variety_id,
                        'variety_name': row.variety_name,
                        'crop_id': row.crop_id,
                        'category_id': row.category_id,
                        'created_at': row.created_at,
                        'updated_at': row.updated_at
                    }
                    result.append(VarietyResponse.model_validate(variety_dict))
                except Exception as e:
                    logger.warning(f"Error validating variety at index {idx} (id: {row.variety_id if hasattr(row, 'variety_id') else 'unknown'}): {str(e)}")
                    continue
            
            logger.info(f"Successfully converted {len(result)} varieties")
            return result
            
        except Exception as e:
            import traceback
            error_msg = f"Error fetching varieties: {str(e)}\n{traceback.format_exc()}"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=f"Failed to fetch varieties: {str(e)}")

    def get_variety_by_id(self, variety_id: str) -> VarietyRecord:
        """
        Get a variety by its ID.
        
        Args:
            variety_id: The variety ID to fetch
            
        Returns:
            VarietyRecord object
            
        Raises:
            HTTPException: If variety not found
        """
        variety = self.db.query(VarietyRecord).filter(
            VarietyRecord.variety_id == variety_id
        ).first()
        
        if not variety:
            raise HTTPException(
                status_code=404,
                detail=f"Variety with ID '{variety_id}' not found"
            )
        
        return variety

    def get_column_metadata(self, category_id: int = 100003) -> List[Dict[str, Any]]:
        """
        Get column metadata for varieties from operations.column_metadata.
        
        Args:
            category_id: Category ID for varieties (default: 100003)
            
        Returns:
            List of column metadata dictionaries
        """
        conn = get_connection()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            
            cur.execute("""
                SELECT TRIM(db_column_name) as db_column_name, display_name, type, group_name, 
                       COALESCE(is_visible, TRUE) as is_visible, 
                       COALESCE(is_editable, FALSE) as is_editable,
                       CASE 
                           WHEN TRIM(db_column_name) = 'variety_id' THEN 1
                           WHEN TRIM(db_column_name) = 'variety_name' THEN 2
                           WHEN TRIM(db_column_name) = 'crop_id' THEN 3
                           WHEN TRIM(db_column_name) = 'category_id' THEN 4
                           WHEN TRIM(db_column_name) = 'created_at' THEN 5
                           WHEN TRIM(db_column_name) = 'updated_at' THEN 6
                           ELSE 999
                       END as custom_order
                FROM operations.column_metadata
                WHERE category_id = %s
                ORDER BY custom_order, column_id
            """, (category_id,))
            
            metadata = cur.fetchall()
            if metadata:
                result = []
                for col in metadata:
                    col_dict = dict(col)
                    if 'db_column_name' in col_dict and col_dict['db_column_name']:
                        col_dict['db_column_name'] = col_dict['db_column_name'].strip()
                    col_dict['is_visible'] = col_dict.get('is_visible', True) if col_dict.get('is_visible') is not None else True
                    col_dict['is_editable'] = col_dict.get('is_editable', False) if col_dict.get('is_editable') is not None else False
                    result.append(col_dict)
                return result
            else:
                # Return default metadata if none exists
                return [
                    {"db_column_name": "variety_id", "display_name": "Variety ID", "type": "string",
                     "group_name": "Identification", "is_visible": True, "is_editable": False},
                    {"db_column_name": "variety_name", "display_name": "Variety Name", "type": "string",
                     "group_name": "Variety", "is_visible": True, "is_editable": True},
                    {"db_column_name": "crop_id", "display_name": "Crop ID", "type": "string",
                     "group_name": "Variety", "is_visible": True, "is_editable": True},
                    {"db_column_name": "category_id", "display_name": "Category ID", "type": "integer",
                     "group_name": "Identification", "is_visible": False, "is_editable": False},
                    {"db_column_name": "created_at", "display_name": "Created At", "type": "timestamp",
                     "group_name": "Audit", "is_visible": False, "is_editable": False},
                    {"db_column_name": "updated_at", "display_name": "Updated At", "type": "timestamp",
                     "group_name": "Audit", "is_visible": False, "is_editable": False}
                ]
        except Exception as e:
            logger.error(f"Error fetching column metadata: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch column metadata: {str(e)}")
        finally:
            if 'cur' in locals():
                cur.close()
            release_connection(conn)

