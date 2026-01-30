import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from core.db import get_db
from services.crop_service import CropService
from models.db_models import CropRecord
from models.schemas.base import (
    CropResponse,
    CropListResponse
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/crops")
async def get_crops(
    db: Session = Depends(get_db),
    crop_name: Optional[str] = Query(None, description="Filter by crop name (case-insensitive search)"),
    limit: int = Query(500, ge=1, le=5000, description="Maximum number of records to return (default: 500, max: 5000 for performance)"),
    offset: int = Query(0, ge=0, description="Number of records to skip")
):
    """
    Get all crops from operations.crops table.
    
    Supports filtering by crop_name (ILIKE search).
    Supports pagination with limit and offset.
    
    Returns:
        CropListResponse with data and columns metadata
    """
    try:
        logger.info(f"Starting crop fetch: limit={limit}, offset={offset}")
        service = CropService(db)
        
        # Get column metadata
        try:
            columns = service.get_column_metadata(category_id=100002)
            logger.info(f"Fetched {len(columns)} column metadata entries")
        except Exception as e:
            logger.warning(f"Error fetching column metadata: {str(e)}, using empty list")
            columns = []
        
        logger.info("Fetching crops...")
        crops = service.get_all_crops(
            crop_name=crop_name,
            limit=limit,
            offset=offset
        )
        logger.info(f"Returning {len(crops)} crops with {len(columns)} columns")
        
        # Return as dict to ensure columns are included
        return {
            "data": [crop.model_dump() for crop in crops],
            "columns": columns
        }
    except HTTPException as he:
        logger.error(f"HTTPException: {he.detail}")
        raise
    except Exception as e:
        import traceback
        error_detail = str(e)
        error_trace = traceback.format_exc()
        logger.error(f"Error fetching crops: {error_detail}\n{error_trace}")
        
        # Provide more helpful error message
        if "timeout" in error_detail.lower() or "timed out" in error_detail.lower():
            error_msg = f"Query timeout. Try reducing limit (current: {limit}) or adding filters."
        elif "connection" in error_detail.lower():
            error_msg = f"Database connection error. Please try again."
        else:
            error_msg = f"Failed to fetch crops: {error_detail[:200]}. Try with a smaller limit (current: {limit}) or add filters."
        
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/crops/dropdown", response_model=Dict[str, Any])
async def get_crops_dropdown(
    db: Session = Depends(get_db)
):
    """
    Get dropdown options for crops.
    
    Returns crop_id and crop_name pairs for dropdown selection.
    
    Returns:
        Dictionary with 'crops' key containing list of {crop_id: crop_name} dictionaries
    """
    try:
        crops = db.query(CropRecord.crop_id, CropRecord.crop_name).filter(
            CropRecord.crop_name.isnot(None),
            CropRecord.crop_name != ''
        ).distinct().order_by(CropRecord.crop_name).all()
        
        result = {
            'crops': [{c.crop_id: c.crop_name} for c in crops if c.crop_name]
        }
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch crop dropdown options: {str(e)}")
