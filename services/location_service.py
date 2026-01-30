import logging
import math
import psycopg2
import psycopg2.extras
from typing import List, Optional, Dict, Any
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from models.db_models import LocationRecord, SeasonCropInspectionBase
from models.schemas.base import LocationUpdateRequest, LocationResponse, LocationSummaryResponse
from core.db import get_connection, release_connection

logger = logging.getLogger(__name__)


def safe_float(value) -> Optional[float]:
    """
    Safely convert a value to float, handling None, inf, -inf, and nan.
    Returns None for invalid values.
    """
    if value is None:
        return None
    try:
        float_val = float(value)
        # Check for invalid float values
        if math.isnan(float_val) or math.isinf(float_val):
            return None
        return float_val
    except (ValueError, TypeError):
        return None


class LocationService:
    """Service for managing location records and syncing related data."""

    def __init__(self, db: Session):
        self.db = db

    def get_all_locations(
        self,
        village: Optional[str] = None,
        mandal: Optional[str] = None,
        district: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[LocationResponse]:
        """
        Fetch all locations from operations.locations table with optional filtering.
        
        Args:
            village: Optional filter for village name
            mandal: Optional filter for mandal name
            district: Optional filter for district name
            state: Optional filter for state name
            limit: Maximum number of records to return (max 5000)
            offset: Number of records to skip
            
        Returns:
            List of LocationResponse objects
        """
        try:
            # Cap the limit to prevent performance issues
            if limit > 5000:
                limit = 5000
                logger.warning(f"Limit capped at 5000 for performance")
            
            logger.info(f"Fetching locations with limit={limit}, offset={offset}")
            
            # Build WHERE conditions
            where_conditions = []
            params = {}
            
            if village:
                where_conditions.append("village ILIKE :village")
                params['village'] = f"%{village}%"
            if mandal:
                where_conditions.append("mandal ILIKE :mandal")
                params['mandal'] = f"%{mandal}%"
            if district:
                where_conditions.append("district ILIKE :district")
                params['district'] = f"%{district}%"
            if state:
                where_conditions.append("state ILIKE :state")
                params['state'] = f"%{state}%"
            
            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
            
            # Use raw SQL for better performance and control
            sql_query = text(f"""
                SELECT 
                    location_id,
                    source_location_id,
                    village,
                    mandal,
                    mandal_id,
                    district,
                    district_id,
                    state,
                    latitude,
                    longitude,
                    category_id,
                    created_at,
                    updated_at
                FROM operations.locations
                WHERE {where_clause}
                ORDER BY location_id
                LIMIT :limit OFFSET :offset
            """)
            
            params['limit'] = limit
            params['offset'] = offset
            
            logger.info("Executing query...")
            result_set = self.db.execute(sql_query, params)
            locations = result_set.fetchall()
            logger.info(f"Query returned {len(locations)} records")
            
            # Convert to response objects
            result = []
            for idx, row in enumerate(locations):
                try:
                    # Handle Row object from raw SQL query
                    loc_dict = {
                        'location_id': row.location_id,
                        'source_location_id': row.source_location_id,
                        'village': row.village or "",
                        'mandal': row.mandal,
                        'mandal_id': row.mandal_id,
                        'district': row.district,
                        'district_id': row.district_id,
                        'state': row.state,
                        'latitude': safe_float(row.latitude),
                        'longitude': safe_float(row.longitude),
                        'category_id': row.category_id,
                        'created_at': row.created_at,
                        'updated_at': row.updated_at
                    }
                    result.append(LocationResponse.model_validate(loc_dict))
                except Exception as e:
                    logger.warning(f"Error validating location at index {idx} (id: {row.location_id if hasattr(row, 'location_id') else 'unknown'}): {str(e)}")
                    continue
            
            logger.info(f"Successfully converted {len(result)} locations")
            return result
            
        except Exception as e:
            import traceback
            error_msg = f"Error fetching locations: {str(e)}\n{traceback.format_exc()}"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=f"Failed to fetch locations: {str(e)}")

    def get_location_summaries(
        self,
        village: Optional[str] = None,
        mandal: Optional[str] = None,
        district: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 1000,
        offset: int = 0
    ) -> List[LocationSummaryResponse]:
        """
        Fetch location summaries with only essential display fields.
        
        Args:
            village: Optional filter for village name
            mandal: Optional filter for mandal name
            district: Optional filter for district name
            state: Optional filter for state name
            limit: Maximum number of records to return (max 5000)
            offset: Number of records to skip
            
        Returns:
            List of LocationSummaryResponse objects with location_id, village, mandal, district, state, latitude, longitude
        """
        try:
            # Cap the limit to prevent performance issues
            if limit > 5000:
                limit = 5000
                logger.warning(f"Limit capped at 5000 for performance")
            
            logger.info(f"Fetching location summaries with limit={limit}, offset={offset}")
            
            # Build WHERE conditions
            where_conditions = []
            params = {}
            
            if village:
                where_conditions.append("village ILIKE :village")
                params['village'] = f"%{village}%"
            if mandal:
                where_conditions.append("mandal ILIKE :mandal")
                params['mandal'] = f"%{mandal}%"
            if district:
                where_conditions.append("district ILIKE :district")
                params['district'] = f"%{district}%"
            if state:
                where_conditions.append("state ILIKE :state")
                params['state'] = f"%{state}%"
            
            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
            
            # Use raw SQL for better performance
            sql_query = text(f"""
                SELECT 
                    location_id,
                    village,
                    mandal,
                    district,
                    state,
                    latitude,
                    longitude
                FROM operations.locations
                WHERE {where_clause}
                ORDER BY village
                LIMIT :limit OFFSET :offset
            """)
            
            params['limit'] = limit
            params['offset'] = offset
            
            logger.info("Executing summary query...")
            result_set = self.db.execute(sql_query, params)
            locations = result_set.fetchall()
            logger.info(f"Summary query returned {len(locations)} records")
            
            # Convert to summary response efficiently
            summaries = []
            for idx, row in enumerate(locations):
                try:
                    summaries.append(LocationSummaryResponse(
                        location_id=row.location_id,
                        village=row.village or "",
                        mandal=row.mandal,
                        district=row.district,
                        state=row.state,
                        latitude=safe_float(row.latitude),
                        longitude=safe_float(row.longitude)
                    ))
                except Exception as e:
                    logger.warning(f"Error creating summary at index {idx}: {str(e)}")
                    continue
            
            logger.info(f"Successfully converted {len(summaries)} summaries")
            return summaries
            
        except Exception as e:
            import traceback
            error_msg = f"Error fetching location summaries: {str(e)}\n{traceback.format_exc()}"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=f"Failed to fetch location summaries: {str(e)}")

    def get_location_by_id(self, location_id: str) -> LocationRecord:
        """
        Get a location by its ID.
        
        Args:
            location_id: The location ID to fetch
            
        Returns:
            LocationRecord object
            
        Raises:
            HTTPException: If location not found
        """
        location = self.db.query(LocationRecord).filter(
            LocationRecord.location_id == location_id
        ).first()
        
        if not location:
            raise HTTPException(
                status_code=404,
                detail=f"Location with ID '{location_id}' not found"
            )
        
        return location

    def update_location(
        self,
        location_id: str,
        update_data: LocationUpdateRequest
    ) -> LocationResponse:
        """
        Update a location and sync related data in season_crop_inspection_base.
        Uses database transaction to ensure atomicity.
        
        Args:
            location_id: The location ID to update
            update_data: LocationUpdateRequest with fields to update
            
        Returns:
            Updated LocationResponse object
            
        Raises:
            HTTPException: If validation fails, location not found, or update fails
        """
        try:
            # Validate that at least one field is provided
            update_dict = update_data.model_dump(exclude_unset=True)
            if not update_dict:
                raise HTTPException(
                    status_code=400,
                    detail="At least one field must be provided for update"
                )
            
            # Get the location
            location = self.get_location_by_id(location_id)
            
            # Validate field values
            self._validate_update_fields(update_dict)
            
            # Start transaction - update operations.locations
            update_fields = {}
            sync_fields = {}
            
            if 'village' in update_dict:
                update_fields['village'] = update_dict['village']
                sync_fields['village'] = update_dict['village']
            
            if 'mandal' in update_dict:
                update_fields['mandal'] = update_dict['mandal']
                sync_fields['mandal'] = update_dict['mandal']
            
            if 'district' in update_dict:
                update_fields['district'] = update_dict['district']
                sync_fields['district'] = update_dict['district']
            
            if 'state' in update_dict:
                update_fields['state'] = update_dict['state']
                sync_fields['state'] = update_dict['state']
            
            if 'latitude' in update_dict:
                update_fields['latitude'] = update_dict['latitude']
            
            if 'longitude' in update_dict:
                update_fields['longitude'] = update_dict['longitude']
            
            # Update the location record
            for field, value in update_fields.items():
                setattr(location, field, value)
            
            # Sync related records in season_crop_inspection_base
            if sync_fields:
                self._sync_season_crop_inspection_base(location_id, sync_fields)
            
            # Commit the transaction
            self.db.commit()
            
            # Refresh the location to get updated data
            self.db.refresh(location)
            
            logger.info(f"Successfully updated location {location_id}")
            
            return LocationResponse.model_validate(location)
            
        except HTTPException:
            # Re-raise HTTP exceptions
            raise
        except Exception as e:
            # Rollback on any error
            self.db.rollback()
            logger.error(f"Error updating location {location_id}: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to update location: {str(e)}"
            )

    def _validate_update_fields(self, update_dict: Dict[str, Any]) -> None:
        """
        Validate update fields.
        
        Args:
            update_dict: Dictionary of fields to update
            
        Raises:
            HTTPException: If validation fails
        """
        # Validate latitude range
        if 'latitude' in update_dict and update_dict['latitude'] is not None:
            lat = update_dict['latitude']
            if not isinstance(lat, (int, float)) or lat < -90 or lat > 90:
                raise HTTPException(
                    status_code=400,
                    detail="Latitude must be between -90 and 90"
                )
        
        # Validate longitude range
        if 'longitude' in update_dict and update_dict['longitude'] is not None:
            lon = update_dict['longitude']
            if not isinstance(lon, (int, float)) or lon < -180 or lon > 180:
                raise HTTPException(
                    status_code=400,
                    detail="Longitude must be between -180 and 180"
                )
        
        # Validate string fields are not empty
        string_fields = ['village', 'mandal', 'district', 'state']
        for field in string_fields:
            if field in update_dict and update_dict[field] is not None:
                if not isinstance(update_dict[field], str) or len(update_dict[field].strip()) == 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{field.capitalize()} cannot be empty"
                    )

    def _sync_season_crop_inspection_base(
        self,
        location_id: str,
        sync_fields: Dict[str, Any]
    ) -> None:
        """
        Update all rows in season_crop_inspection_base that reference this location_id.
        Uses efficient bulk update.
        
        Args:
            location_id: The location ID to sync
            sync_fields: Dictionary of fields to sync (village, mandal, district, state)
        """
        try:
            # Build the UPDATE query dynamically
            set_clauses = []
            params = {}
            
            for field in ['village', 'mandal', 'district', 'state']:
                if field in sync_fields:
                    set_clauses.append(f"{field} = :{field}")
                    params[field] = sync_fields[field]
            
            if not set_clauses:
                return  # Nothing to sync
            
            # Add location_id to params
            params['location_id'] = location_id
            
            # Build and execute the UPDATE query
            update_query = text(f"""
                UPDATE operations.season_crop_inspection_base
                SET {', '.join(set_clauses)}
                WHERE location_id = :location_id
            """)
            
            # Execute with parameters as dictionary
            result = self.db.execute(update_query, params)
            rows_updated = result.rowcount
            
            logger.info(
                f"Synced {rows_updated} rows in season_crop_inspection_base "
                f"for location_id {location_id}"
            )
            
        except Exception as e:
            logger.error(
                f"Error syncing season_crop_inspection_base for location {location_id}: {str(e)}"
            )
            raise

    def get_column_metadata(self, category_id: int = 100004) -> List[Dict[str, Any]]:
        """
        Get column metadata for locations from operations.column_metadata.
        
        Args:
            category_id: Category ID for locations (default: 100004)
            
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
                           WHEN TRIM(db_column_name) = 'location_id' THEN 1
                           WHEN TRIM(db_column_name) = 'source_location_id' THEN 2
                           WHEN TRIM(db_column_name) = 'village' THEN 3
                           WHEN TRIM(db_column_name) = 'mandal' THEN 4
                           WHEN TRIM(db_column_name) = 'district' THEN 5
                           WHEN TRIM(db_column_name) = 'state' THEN 6
                           WHEN TRIM(db_column_name) = 'latitude' THEN 7
                           WHEN TRIM(db_column_name) = 'longitude' THEN 8
                           WHEN TRIM(db_column_name) = 'mandal_id' THEN 9
                           WHEN TRIM(db_column_name) = 'district_id' THEN 10
                           WHEN TRIM(db_column_name) = 'category_id' THEN 11
                           WHEN TRIM(db_column_name) = 'created_at' THEN 12
                           WHEN TRIM(db_column_name) = 'updated_at' THEN 13
                           ELSE 999
                       END as custom_order
                FROM operations.column_metadata
                WHERE category_id = %s
                ORDER BY custom_order, column_id
            """, (category_id,))
            
            metadata = cur.fetchall()
            if metadata:
                # Ensure db_column_name is trimmed and is_editable/is_visible have defaults
                result = []
                for col in metadata:
                    col_dict = dict(col)
                    # Trim db_column_name if it exists
                    if 'db_column_name' in col_dict and col_dict['db_column_name']:
                        col_dict['db_column_name'] = col_dict['db_column_name'].strip()
                    # Ensure boolean values are set
                    col_dict['is_visible'] = col_dict.get('is_visible', True) if col_dict.get('is_visible') is not None else True
                    col_dict['is_editable'] = col_dict.get('is_editable', False) if col_dict.get('is_editable') is not None else False
                    result.append(col_dict)
                return result
            else:
                # Return default metadata if none exists
                return [
                    {"db_column_name": "location_id", "display_name": "Location ID", "type": "string",
                     "group_name": "Identification", "is_visible": True, "is_editable": False},
                    {"db_column_name": "source_location_id", "display_name": "Source Location ID", "type": "string",
                     "group_name": "Identification", "is_visible": False, "is_editable": False},
                    {"db_column_name": "village", "display_name": "Village", "type": "string",
                     "group_name": "Location", "is_visible": True, "is_editable": True},
                    {"db_column_name": "mandal", "display_name": "Mandal", "type": "string",
                     "group_name": "Location", "is_visible": True, "is_editable": True},
                    {"db_column_name": "district", "display_name": "District", "type": "string",
                     "group_name": "Location", "is_visible": True, "is_editable": True},
                    {"db_column_name": "state", "display_name": "State", "type": "string",
                     "group_name": "Location", "is_visible": True, "is_editable": True},
                    {"db_column_name": "latitude", "display_name": "Latitude", "type": "decimal",
                     "group_name": "Coordinates", "is_visible": True, "is_editable": True},
                    {"db_column_name": "longitude", "display_name": "Longitude", "type": "decimal",
                     "group_name": "Coordinates", "is_visible": True, "is_editable": True},
                    {"db_column_name": "mandal_id", "display_name": "Mandal ID", "type": "integer",
                     "group_name": "Identification", "is_visible": False, "is_editable": False},
                    {"db_column_name": "district_id", "display_name": "District ID", "type": "integer",
                     "group_name": "Identification", "is_visible": False, "is_editable": False},
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

