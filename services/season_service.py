import logging
import psycopg2
import psycopg2.extras
from typing import List, Optional, Dict, Any
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from models.db_models import SeasonRecord
from models.schemas.base import SeasonUpdateRequest, SeasonResponse
from core.db import get_connection, release_connection

logger = logging.getLogger(__name__)


class SeasonService:
    """Service for managing season records."""

    def __init__(self, db: Session):
        self.db = db

    def get_all_seasons(
        self,
        season_name: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[SeasonResponse]:
        """
        Fetch all seasons from operations.seasons table with optional filtering.
        
        Args:
            season_name: Optional filter for season name (ILIKE search)
            limit: Maximum number of records to return (max 5000)
            offset: Number of records to skip
            
        Returns:
            List of SeasonResponse objects
        """
        try:
            # Cap the limit to prevent performance issues
            if limit > 5000:
                limit = 5000
                logger.warning(f"Limit capped at 5000 for performance")
            
            logger.info(f"Fetching seasons with limit={limit}, offset={offset}")
            
            # Build WHERE conditions
            where_conditions = []
            params = {}
            
            if season_name:
                where_conditions.append("season_name ILIKE :season_name")
                params['season_name'] = f"%{season_name}%"
            
            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
            
            # Use raw SQL for better performance and control
            sql_query = text(f"""
                SELECT 
                    season_id,
                    season_name,
                    start_date,
                    end_date,
                    description,
                    is_default,
                    status,
                    create_date
                FROM operations.seasons
                WHERE {where_clause}
                ORDER BY season_id
                LIMIT :limit OFFSET :offset
            """)
            
            params['limit'] = limit
            params['offset'] = offset
            
            logger.info("Executing query...")
            result_set = self.db.execute(sql_query, params)
            seasons = result_set.fetchall()
            logger.info(f"Query returned {len(seasons)} records")
            
            # Convert to response objects
            result = []
            for idx, row in enumerate(seasons):
                try:
                    # Handle Row object from raw SQL query
                    season_dict = {
                        'season_id': row.season_id,
                        'season_name': getattr(row, 'season_name', None),
                        'start_date': getattr(row, 'start_date', None),
                        'end_date': getattr(row, 'end_date', None),
                        'description': getattr(row, 'description', None),
                        'is_default': getattr(row, 'is_default', None),
                        'status': getattr(row, 'status', None),
                        'create_date': getattr(row, 'create_date', None)
                    }
                    result.append(SeasonResponse.model_validate(season_dict))
                except Exception as e:
                    logger.warning(f"Error validating season at index {idx} (id: {row.season_id if hasattr(row, 'season_id') else 'unknown'}): {str(e)}")
                    continue
            
            logger.info(f"Successfully converted {len(result)} seasons")
            return result
            
        except Exception as e:
            import traceback
            error_msg = f"Error fetching seasons: {str(e)}\n{traceback.format_exc()}"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=f"Failed to fetch seasons: {str(e)}")

    def get_season_by_id(self, season_id: str) -> SeasonRecord:
        """
        Get a season by its ID.
        
        Args:
            season_id: The season ID to fetch
            
        Returns:
            SeasonRecord object
            
        Raises:
            HTTPException: If season not found
        """
        season = self.db.query(SeasonRecord).filter(
            SeasonRecord.season_id == season_id
        ).first()
        
        if not season:
            raise HTTPException(
                status_code=404,
                detail=f"Season with ID '{season_id}' not found"
            )
        
        return season

    def update_season(
        self,
        season_id: str,
        update_data: SeasonUpdateRequest
    ) -> SeasonResponse:
        """
        Update a season record.
        Uses database transaction to ensure atomicity.
        
        Args:
            season_id: The season ID to update
            update_data: SeasonUpdateRequest with fields to update
            
        Returns:
            Updated SeasonResponse object
            
        Raises:
            HTTPException: If validation fails, season not found, or update fails
        """
        try:
            # Validate that at least one field is provided
            update_dict = update_data.model_dump(exclude_unset=True)
            if not update_dict:
                raise HTTPException(
                    status_code=400,
                    detail="At least one field must be provided for update"
                )
            
            # Get the season
            season = self.get_season_by_id(season_id)
            
            # Validate field values
            self._validate_update_fields(update_dict)
            
            # Update fields
            for field, value in update_dict.items():
                if hasattr(season, field):
                    setattr(season, field, value)
            
            # Commit the transaction
            self.db.commit()
            
            # Refresh the season to get updated data
            self.db.refresh(season)
            
            logger.info(f"Successfully updated season {season_id}")
            
            # Convert to response
            season_dict = {
                'season_id': season.season_id,
                'season_name': getattr(season, 'season_name', None),
                'start_date': getattr(season, 'start_date', None),
                'end_date': getattr(season, 'end_date', None),
                'description': getattr(season, 'description', None),
                'is_default': getattr(season, 'is_default', None),
                'status': getattr(season, 'status', None),
                'create_date': getattr(season, 'create_date', None)
            }
            return SeasonResponse.model_validate(season_dict)
            
        except HTTPException:
            # Re-raise HTTP exceptions
            raise
        except Exception as e:
            # Rollback on any error
            self.db.rollback()
            logger.error(f"Error updating season {season_id}: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to update season: {str(e)}"
            )

    def _validate_update_fields(self, update_dict: Dict[str, Any]) -> None:
        """
        Validate update fields.
        
        Args:
            update_dict: Dictionary of fields to update
            
        Raises:
            HTTPException: If validation fails
        """
        # Validate date fields
        if 'start_date' in update_dict and 'end_date' in update_dict:
            if update_dict['start_date'] and update_dict['end_date']:
                if update_dict['start_date'] > update_dict['end_date']:
                    raise HTTPException(
                        status_code=400,
                        detail="Start date must be before end date"
                    )
        
        # Validate string fields are not empty
        string_fields = ['season_name', 'description']
        for field in string_fields:
            if field in update_dict and update_dict[field] is not None:
                if not isinstance(update_dict[field], str) or len(update_dict[field].strip()) == 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{field.capitalize()} cannot be empty"
                    )

    def get_column_metadata(self, category_id: int = 100001) -> List[Dict[str, Any]]:
        """
        Get column metadata for seasons from operations.column_metadata.
        
        Args:
            category_id: Category ID for seasons (default: 100001)
            
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
                           WHEN TRIM(db_column_name) = 'season_id' THEN 1
                           WHEN TRIM(db_column_name) = 'season_name' THEN 2
                           WHEN TRIM(db_column_name) = 'start_date' THEN 3
                           WHEN TRIM(db_column_name) = 'end_date' THEN 4
                           WHEN TRIM(db_column_name) = 'description' THEN 5
                           WHEN TRIM(db_column_name) = 'is_default' THEN 6
                           WHEN TRIM(db_column_name) = 'status' THEN 7
                           WHEN TRIM(db_column_name) = 'create_date' THEN 8
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
                    {"db_column_name": "season_id", "display_name": "Season ID", "type": "string",
                     "group_name": "Identification", "is_visible": True, "is_editable": False},
                    {"db_column_name": "season_name", "display_name": "Season Name", "type": "string",
                     "group_name": "Season", "is_visible": True, "is_editable": True},
                    {"db_column_name": "start_date", "display_name": "Start Date", "type": "date",
                     "group_name": "Season", "is_visible": True, "is_editable": True},
                    {"db_column_name": "end_date", "display_name": "End Date", "type": "date",
                     "group_name": "Season", "is_visible": True, "is_editable": True},
                    {"db_column_name": "description", "display_name": "Description", "type": "string",
                     "group_name": "Season", "is_visible": False, "is_editable": True},
                    {"db_column_name": "is_default", "display_name": "Is Default", "type": "boolean",
                     "group_name": "Season", "is_visible": False, "is_editable": True},
                    {"db_column_name": "status", "display_name": "Status", "type": "boolean",
                     "group_name": "Season", "is_visible": False, "is_editable": True},
                    {"db_column_name": "create_date", "display_name": "Create Date", "type": "timestamp",
                     "group_name": "Audit", "is_visible": False, "is_editable": False}
                ]
        except Exception as e:
            logger.error(f"Error fetching column metadata: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch column metadata: {str(e)}")
        finally:
            if 'cur' in locals():
                cur.close()
            release_connection(conn)

