import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from core.db import get_db
from services.variety_service import VarietyService
from models.db_models import VarietyRecord
from models.schemas.base import (
    VarietyResponse,
    VarietyListResponse
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/varieties")
async def get_varieties(
    db: Session = Depends(get_db),
    variety_name: Optional[str] = Query(None, description="Filter by variety name (case-insensitive search)"),
    crop_id: Optional[str] = Query(None, description="Filter by crop ID"),
    limit: int = Query(500, ge=1, le=5000, description="Maximum number of records to return (default: 500, max: 5000 for performance)"),
    offset: int = Query(0, ge=0, description="Number of records to skip")
):
    """
    Get all varieties from operations.varieties table.
    
    Supports filtering by variety_name (ILIKE search) and crop_id.
    Supports pagination with limit and offset.
    
    Returns:
        VarietyListResponse with data and columns metadata
    """
    try:
        logger.info(f"Starting variety fetch: limit={limit}, offset={offset}")
        service = VarietyService(db)
        
        # Get column metadata
        try:
            columns = service.get_column_metadata(category_id=100003)
            logger.info(f"Fetched {len(columns)} column metadata entries")
        except Exception as e:
            logger.warning(f"Error fetching column metadata: {str(e)}, using empty list")
            columns = []
        
        logger.info("Fetching varieties...")
        varieties = service.get_all_varieties(
            variety_name=variety_name,
            crop_id=crop_id,
            limit=limit,
            offset=offset
        )
        logger.info(f"Returning {len(varieties)} varieties with {len(columns)} columns")
        
        # Return as dict to ensure columns are included
        return {
            "data": [variety.model_dump() for variety in varieties],
            "columns": columns
        }
    except HTTPException as he:
        logger.error(f"HTTPException: {he.detail}")
        raise
    except Exception as e:
        import traceback
        error_detail = str(e)
        error_trace = traceback.format_exc()
        logger.error(f"Error fetching varieties: {error_detail}\n{error_trace}")
        
        # Provide more helpful error message
        if "timeout" in error_detail.lower() or "timed out" in error_detail.lower():
            error_msg = f"Query timeout. Try reducing limit (current: {limit}) or adding filters."
        elif "connection" in error_detail.lower():
            error_msg = f"Database connection error. Please try again."
        else:
            error_msg = f"Failed to fetch varieties: {error_detail[:200]}. Try with a smaller limit (current: {limit}) or add filters."
        
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/varieties/dropdown", response_model=Dict[str, Any])
async def get_varieties_dropdown(
    db: Session = Depends(get_db),
    crop_id: Optional[str] = Query(None, description="Filter varieties by crop_id (required for dependent dropdown)")
):
    """
    Get dropdown options for varieties.
    
    Returns variety_id and variety_name pairs for dropdown selection.
    If crop_id is provided, returns only varieties for that crop (dependent dropdown).
    
    Args:
        crop_id: Optional crop ID to filter varieties (for dependent dropdown)
    
    Returns:
        Dictionary with 'varieties' key containing list of {variety_id: variety_name} dictionaries
    """
    try:
        query = db.query(VarietyRecord.variety_id, VarietyRecord.variety_name).filter(
            VarietyRecord.variety_name.isnot(None),
            VarietyRecord.variety_name != ''
        )
        
        if crop_id:
            query = query.filter(VarietyRecord.crop_id == crop_id)
        
        varieties = query.distinct().order_by(VarietyRecord.variety_name).all()
        
        result = {
            'varieties': [{v.variety_id: v.variety_name} for v in varieties if v.variety_name]
        }
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch variety dropdown options: {str(e)}")
