import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from core.db import get_db
from services.season_service import SeasonService
from models.db_models import SeasonRecord
from models.schemas.base import (
    SeasonUpdateRequest,
    SeasonUpdateResponse,
    SeasonResponse,
    SeasonListResponse
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/seasons")
async def get_seasons(
    db: Session = Depends(get_db),
    season_name: Optional[str] = Query(None, description="Filter by season name (case-insensitive search)"),
    limit: int = Query(500, ge=1, le=5000, description="Maximum number of records to return (default: 500, max: 5000 for performance)"),
    offset: int = Query(0, ge=0, description="Number of records to skip")
):
    """
    Get all seasons from operations.seasons table.
    
    Supports filtering by season_name (ILIKE search).
    Supports pagination with limit and offset.
    
    Returns:
        SeasonListResponse with data and columns metadata
    """
    try:
        logger.info(f"Starting season fetch: limit={limit}, offset={offset}")
        service = SeasonService(db)
        
        # Get column metadata
        try:
            columns = service.get_column_metadata(category_id=100001)
            logger.info(f"Fetched {len(columns)} column metadata entries")
        except Exception as e:
            logger.warning(f"Error fetching column metadata: {str(e)}, using empty list")
            columns = []
        
        logger.info("Fetching seasons...")
        seasons = service.get_all_seasons(
            season_name=season_name,
            limit=limit,
            offset=offset
        )
        logger.info(f"Returning {len(seasons)} seasons with {len(columns)} columns")
        
        # Return as dict to ensure columns are included
        return {
            "data": [season.model_dump() for season in seasons],
            "columns": columns
        }
    except HTTPException as he:
        logger.error(f"HTTPException: {he.detail}")
        raise
    except Exception as e:
        import traceback
        error_detail = str(e)
        error_trace = traceback.format_exc()
        logger.error(f"Error fetching seasons: {error_detail}\n{error_trace}")
        
        # Provide more helpful error message
        if "timeout" in error_detail.lower() or "timed out" in error_detail.lower():
            error_msg = f"Query timeout. Try reducing limit (current: {limit}) or adding filters."
        elif "connection" in error_detail.lower():
            error_msg = f"Database connection error. Please try again."
        else:
            error_msg = f"Failed to fetch seasons: {error_detail[:200]}. Try with a smaller limit (current: {limit}) or add filters."
        
        raise HTTPException(status_code=500, detail=error_msg)


@router.put("/seasons/{season_id}", response_model=SeasonUpdateResponse)
async def update_season(
    season_id: str = Path(..., description="The season ID to update"),
    update_data: SeasonUpdateRequest = ...,
    db: Session = Depends(get_db)
):
    """
    Update a season in operations.seasons table.
    
    Updates the specified fields in the season record.
    
    Uses a database transaction to ensure atomicity - if any update fails,
    all changes are rolled back.
    
    Args:
        season_id: The season ID to update
        update_data: SeasonUpdateRequest with fields to update
        
    Returns:
        SeasonUpdateResponse with success message and updated season data
        
    Raises:
        400: Validation error (missing or invalid fields)
        404: Season not found
        500: Database error, transaction failure, or unexpected issue
    """
    try:
        service = SeasonService(db)
        updated_season = service.update_season(season_id, update_data)
        
        # Get column metadata
        try:
            columns = service.get_column_metadata(category_id=100001)
        except Exception as e:
            logger.warning(f"Error fetching column metadata: {str(e)}, using empty list")
            columns = []
        
        # Return as dict to ensure columns are included
        return {
            "message": "Season updated successfully",
            "season": updated_season.model_dump(),
            "columns": columns
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update season: {str(e)}")


@router.get("/seasons/dropdown", response_model=Dict[str, Any])
async def get_seasons_dropdown(
    db: Session = Depends(get_db)
):
    """
    Get dropdown options for seasons.
    
    Returns unique season names for dropdown selection.
    
    Returns:
        Dictionary with 'season_name' key containing list of unique season names
    """
    try:
        seasons = db.query(SeasonRecord.season_name).filter(
            SeasonRecord.season_name.isnot(None),
            SeasonRecord.season_name != ''
        ).distinct().order_by(SeasonRecord.season_name).all()
        
        result = {
            'season_name': [s[0] for s in seasons if s[0]]
        }
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch season dropdown options: {str(e)}")
