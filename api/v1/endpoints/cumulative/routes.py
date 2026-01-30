"""
FastAPI routes for cumulative inspection data endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from core.db import get_db

from .services import CumulativeInspectionService

router = APIRouter()


# Response models
class CumulativeInspectionResponse(BaseModel):
    """Response model for cumulative inspection data"""
    inspection_level: int = Field(..., description="The inspection level (1-6)")
    total_lots: int = Field(..., description="Number of lots returned in this response")
    limit: int = Field(..., description="Maximum number of records requested")
    offset: int = Field(..., description="Number of records skipped")
    has_more: bool = Field(..., description="Whether more data is available")
    data: list = Field(..., description="List of inspection records")
    
    class Config:
        json_schema_extra = {
            "example": {
                "inspection_level": 3,
                "total_lots": 100,
                "limit": 100,
                "offset": 0,
                "has_more": True,
                "data": [
                    {
                        "lot_no": "LOT001",
                        "inspection_date": "2024-01-15T10:30:00",
                        # ... other fields from joined tables
                    }
                ]
            }
        }


@router.get(
    "/inspections/sheet/{level}",
    response_model=CumulativeInspectionResponse,
    summary="Get cumulative inspection data",
    description="""
    Returns cumulative inspection data by joining inspection_level_1 with subsequent levels.
    
    - **Sheet 1**: Only inspection_level_1
    - **Sheet 2**: INNER JOIN inspection_level_1 and inspection_level_2 on lot_no
    - **Sheet 3**: INNER JOIN inspection_level_1, 2, 3 on lot_no
    - **Sheet 4**: INNER JOIN inspection_level_1, 2, 3, 4 on lot_no
    - **Sheet 5**: INNER JOIN inspection_level_1, 2, 3, 4, 5 on lot_no
    - **Sheet 6**: INNER JOIN inspection_level_1 through inspection_level_6 on lot_no
    
    Each inspection level is compared only with inspection_level_1 (the anchor table),
    ensuring only lot numbers that exist in inspection_level_1 are included.
    
    **Pagination**: Use `limit` and `offset` query parameters for pagination.
    - `limit`: Maximum number of records to return (default: 100, max: 1000)
    - `offset`: Number of records to skip (default: 0)
    """,
    responses={
        200: {
            "description": "Successfully retrieved cumulative inspection data",
            "content": {
                "application/json": {
                    "example": {
                        "inspection_level": 3,
                        "total_lots": 150,
                        "data": [
                            {
                                "lot_no": "LOT001",
                                "inspection_date": "2024-01-15T10:30:00"
                            }
                        ]
                    }
                }
            }
        },
        400: {
            "description": "Invalid inspection level",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Inspection level must be between 1 and 6, got 7"
                    }
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Failed to fetch cumulative inspection data: ..."
                    }
                }
            }
        }
    }
)
async def get_cumulative_inspection_sheet(
    level: int = Path(
        ...,
        ge=1,
        le=6,
        description="Inspection level (1-6)",
        example=3
    ),
    limit: int = Query(
        100,
        ge=1,
        le=1000,
        description="Maximum number of records to return (default: 100, max: 1000)",
        example=100
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Number of records to skip for pagination (default: 0)",
        example=0
    ),
    db: Session = Depends(get_db)
) -> CumulativeInspectionResponse:
    """
    Get cumulative inspection data for a specific level with pagination
    
    Args:
        level: Inspection level (1-6)
        limit: Maximum number of records to return (default: 100, max: 1000)
        offset: Number of records to skip (default: 0)
        db: Database session
        
    Returns:
        CumulativeInspectionResponse with inspection data
        
    Raises:
        HTTPException: If level is invalid or query fails
    """
    try:
        # Validate level
        if level < 1 or level > 6:
            raise HTTPException(
                status_code=400,
                detail=f"Inspection level must be between 1 and 6, got {level}"
            )
        
        # Validate pagination parameters
        if limit < 1 or limit > 1000:
            raise HTTPException(
                status_code=400,
                detail=f"Limit must be between 1 and 1000, got {limit}"
            )
        if offset < 0:
            raise HTTPException(
                status_code=400,
                detail=f"Offset must be non-negative, got {offset}"
            )
        
        # Create service instance and fetch data
        service = CumulativeInspectionService(db=db)
        result = service.get_cumulative_inspection_data(level, limit=limit, offset=offset)
        
        return CumulativeInspectionResponse(**result)
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch cumulative inspection data: {str(e)}"
        )

