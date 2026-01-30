import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.orm import Session
from typing import List, Optional, Union, Dict, Any
from core.db import get_db
from services.location_service import LocationService
from models.db_models import LocationRecord
from models.schemas.base import (
    LocationUpdateRequest, 
    LocationUpdateResponse, 
    LocationResponse, 
    LocationListResponse,
    LocationSummaryResponse,
    LocationSummaryListResponse
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/locations")
async def get_locations(
    db: Session = Depends(get_db),
    village: Optional[str] = Query(None, description="Filter by village name"),
    mandal: Optional[str] = Query(None, description="Filter by mandal name"),
    district: Optional[str] = Query(None, description="Filter by district name"),
    state: Optional[str] = Query(None, description="Filter by state name"),
    limit: int = Query(500, ge=1, le=5000, description="Maximum number of records to return (default: 500, max: 5000 for performance)"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    summary: bool = Query(False, description="Return only essential fields (village, mandal, district, state, latitude, longitude)")
):
    """
    Get all locations from operations.locations table.
    
    Supports filtering by village, mandal, district, and state.
    Supports pagination with limit and offset.
    
    If summary=True, returns only essential display fields:
    - location_id, village, mandal, district, state, latitude, longitude
    
    Returns:
        LocationListResponse (full data) or LocationSummaryListResponse (if summary=True)
    """
    try:
        logger.info(f"Starting location fetch: summary={summary}, limit={limit}, offset={offset}")
        service = LocationService(db)
        
        # Get column metadata
        try:
            columns = service.get_column_metadata(category_id=100004)
            logger.info(f"Fetched {len(columns)} column metadata entries")
        except Exception as e:
            logger.warning(f"Error fetching column metadata: {str(e)}, using empty list")
            columns = []
        
        if summary:
            logger.info("Fetching location summaries...")
            summaries = service.get_location_summaries(
                village=village,
                mandal=mandal,
                district=district,
                state=state,
                limit=limit,
                offset=offset
            )
            logger.info(f"Returning {len(summaries)} summaries with {len(columns)} columns")
            # Return as dict to ensure columns are included
            return {
                "data": [summary.model_dump() for summary in summaries],
                "columns": columns
            }
        else:
            logger.info("Fetching full locations...")
            locations = service.get_all_locations(
                village=village,
                mandal=mandal,
                district=district,
                state=state,
                limit=limit,
                offset=offset
            )
            logger.info(f"Returning {len(locations)} locations with {len(columns)} columns")
            # Return as dict to ensure columns are included
            return {
                "data": [location.model_dump() for location in locations],
                "columns": columns
            }
    except HTTPException as he:
        logger.error(f"HTTPException: {he.detail}")
        raise
    except Exception as e:
        import traceback
        error_detail = str(e)
        error_trace = traceback.format_exc()
        logger.error(f"Error fetching locations: {error_detail}\n{error_trace}")
        
        # Provide more helpful error message
        if "timeout" in error_detail.lower() or "timed out" in error_detail.lower():
            error_msg = f"Query timeout. Try reducing limit (current: {limit}) or adding filters."
        elif "connection" in error_detail.lower():
            error_msg = f"Database connection error. Please try again."
        else:
            error_msg = f"Failed to fetch locations: {error_detail[:200]}. Try with a smaller limit (current: {limit}) or add filters."
        
        raise HTTPException(status_code=500, detail=error_msg)


@router.put("/locations/{location_id}", response_model=LocationUpdateResponse)
async def update_location(
    location_id: str = Path(..., description="The location ID to update"),
    update_data: LocationUpdateRequest = ...,
    db: Session = Depends(get_db)
):
    """
    Update a location in operations.locations table.
    
    Updates the specified fields in the location record and automatically
    syncs the changes to all related records in season_crop_inspection_base.
    
    Uses a database transaction to ensure atomicity - if any update fails,
    all changes are rolled back.
    
    Args:
        location_id: The location ID to update
        update_data: LocationUpdateRequest with fields to update
        
    Returns:
        LocationUpdateResponse with success message and updated location data
        
    Raises:
        400: Validation error (missing or invalid fields)
        404: Location not found
        500: Database error, transaction failure, or unexpected issue
    """
    try:
        service = LocationService(db)
        updated_location = service.update_location(location_id, update_data)
        
        # Get column metadata
        try:
            columns = service.get_column_metadata(category_id=100004)
        except Exception as e:
            logger.warning(f"Error fetching column metadata: {str(e)}, using empty list")
            columns = []
        
        # Return as dict to ensure columns are included
        return {
            "message": "Location updated successfully",
            "location": updated_location.model_dump(),
            "columns": columns
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update location: {str(e)}")


@router.get("/locations/dropdown", response_model=Dict[str, Any])
async def get_locations_dropdown(
    db: Session = Depends(get_db),
    field: Optional[str] = Query(None, description="Specific field to return: 'state', 'district', 'mandal', 'village'. If not provided, returns all unique values for each field")
):
    """
    Get dropdown options for locations.
    
    Returns unique values for state, district, mandal, and village fields.
    Can filter by a specific field to return only that field's options.
    
    Usage:
    - GET /api/v1/locations/dropdown - Returns all fields
    - GET /api/v1/locations/dropdown?field=state - Returns only states
    - GET /api/v1/locations/dropdown?field=district - Returns only districts
    - GET /api/v1/locations/dropdown?field=mandal - Returns only mandals
    - GET /api/v1/locations/dropdown?field=village - Returns only villages
    
    Returns:
        Dictionary with field names as keys and lists of unique values as values
    """
    try:
        result = {}
        
        if field:
            # Return only the requested field
            field_lower = field.lower()
            
            if field_lower == 'state':
                states = db.query(LocationRecord.state).filter(
                    LocationRecord.state.isnot(None),
                    LocationRecord.state != ''
                ).distinct().order_by(LocationRecord.state).all()
                result['state'] = [s[0] for s in states if s[0]]
                
            elif field_lower == 'district':
                districts = db.query(LocationRecord.district).filter(
                    LocationRecord.district.isnot(None),
                    LocationRecord.district != ''
                ).distinct().order_by(LocationRecord.district).all()
                result['district'] = [d[0] for d in districts if d[0]]
                
            elif field_lower == 'mandal':
                mandals = db.query(LocationRecord.mandal).filter(
                    LocationRecord.mandal.isnot(None),
                    LocationRecord.mandal != ''
                ).distinct().order_by(LocationRecord.mandal).all()
                result['mandal'] = [m[0] for m in mandals if m[0]]
                
            elif field_lower == 'village':
                villages = db.query(LocationRecord.village).filter(
                    LocationRecord.village.isnot(None),
                    LocationRecord.village != ''
                ).distinct().order_by(LocationRecord.village).all()
                result['village'] = [v[0] for v in villages if v[0]]
                
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid field '{field}'. Valid fields are: state, district, mandal, village"
                )
        else:
            # Return all fields
            states = db.query(LocationRecord.state).filter(
                LocationRecord.state.isnot(None),
                LocationRecord.state != ''
            ).distinct().order_by(LocationRecord.state).all()
            result['state'] = [s[0] for s in states if s[0]]
            
            districts = db.query(LocationRecord.district).filter(
                LocationRecord.district.isnot(None),
                LocationRecord.district != ''
            ).distinct().order_by(LocationRecord.district).all()
            result['district'] = [d[0] for d in districts if d[0]]
            
            mandals = db.query(LocationRecord.mandal).filter(
                LocationRecord.mandal.isnot(None),
                LocationRecord.mandal != ''
            ).distinct().order_by(LocationRecord.mandal).all()
            result['mandal'] = [m[0] for m in mandals if m[0]]
            
            villages = db.query(LocationRecord.village).filter(
                LocationRecord.village.isnot(None),
                LocationRecord.village != ''
            ).distinct().order_by(LocationRecord.village).all()
            result['village'] = [v[0] for v in villages if v[0]]
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch location dropdown options: {str(e)}")

