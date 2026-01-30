"""
Bhuvan API Polygon Fetching Service

This module fetches village-level polygon (boundary) data from the Bhuvan API
using administrative details stored in the database.

Features:
- Reads unique records from operations.locations
- Constructs Bhuvan API requests with village, mandal, district, state
- Handles HTTP errors, empty responses, and partial matches
- Retry logic (max 2 retries)
- Validates and normalizes polygon geometry to GeoJSON
- Stores results in operations.location_polygons table
- Prevents duplicate records
"""

import logging
import time
import json
import requests
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import text
import psycopg2
import psycopg2.extras
from psycopg2.extras import Json
from core.db import get_connection, release_connection

logger = logging.getLogger(__name__)

# Bhuvan API Configuration
BHUVAN_API_BASE_URL = "https://bhuvan-app1.nrsc.gov.in/api"
BHUVAN_VILLAGE_POLYGON_ENDPOINT = "/village/boundary"  # Assumed endpoint - adjust per actual API docs
ACCESS_TOKEN = "709492afd90de061e1a078ac40647b82b9bf8fa0"

# Rate limiting: sleep between API calls (seconds)
API_CALL_DELAY = 0.5

# Retry configuration
MAX_RETRIES = 2
RETRY_DELAY = 2  # seconds


class BhuvanPolygonService:
    """Service for fetching and storing village polygons from Bhuvan API."""

    def __init__(self, db: Session, access_token: str = ACCESS_TOKEN):
        """
        Initialize the Bhuvan Polygon Service.
        
        Args:
            db: SQLAlchemy database session
            access_token: Bhuvan API access token
        """
        self.db = db
        self.access_token = access_token
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

    def fetch_all_polygons(
        self,
        batch_size: int = 100,
        skip_existing: bool = True,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Fetch polygons for all unique villages in operations.locations.
        
        Args:
            batch_size: Number of records to process in each batch
            skip_existing: Skip villages that already have polygons
            dry_run: If True, only log what would be done without making API calls
            
        Returns:
            Dictionary with statistics about the operation
        """
        logger.info("=" * 80)
        logger.info("Starting Bhuvan polygon fetching process")
        logger.info(f"Batch size: {batch_size}, Skip existing: {skip_existing}, Dry run: {dry_run}")
        logger.info("=" * 80)

        stats = {
            "total_processed": 0,
            "successful": 0,
            "failed": 0,
            "skipped": 0,
            "duplicates": 0,
            "errors": []
        }

        try:
            # Get unique location records
            unique_locations = self._get_unique_locations(skip_existing=skip_existing)
            total_locations = len(unique_locations)
            
            logger.info(f"Found {total_locations} unique locations to process")
            
            if total_locations == 0:
                logger.info("No locations to process")
                return stats

            # Process in batches
            for batch_start in range(0, total_locations, batch_size):
                batch_end = min(batch_start + batch_size, total_locations)
                batch = unique_locations[batch_start:batch_end]
                
                logger.info(f"Processing batch {batch_start + 1}-{batch_end} of {total_locations}")
                
                for location in batch:
                    stats["total_processed"] += 1
                    
                    try:
                        # Skip if village or district is null
                        if not location.get('village') or not location.get('district'):
                            logger.warning(
                                f"Skipping location {location['location_id']}: "
                                f"village or district is null"
                            )
                            stats["skipped"] += 1
                            continue

                        # Check for duplicates
                        if self._polygon_exists(location['location_id']):
                            logger.debug(
                                f"Polygon already exists for location_id: {location['location_id']}"
                            )
                            stats["duplicates"] += 1
                            continue

                        if dry_run:
                            logger.info(
                                f"[DRY RUN] Would fetch polygon for: "
                                f"{location['village']}, {location['mandal']}, "
                                f"{location['district']}, {location['state']}"
                            )
                            stats["successful"] += 1
                            continue

                        # Fetch polygon from API
                        result = self._fetch_polygon_from_api(location)
                        
                        if result['success']:
                            # Store polygon in database
                            self._store_polygon(location, result['geometry'])
                            stats["successful"] += 1
                            logger.info(
                                f"✓ Successfully fetched and stored polygon for "
                                f"{location['village']}, {location['district']}, {location['state']}"
                            )
                        else:
                            stats["failed"] += 1
                            error_msg = (
                                f"Failed to fetch polygon for location_id {location['location_id']}: "
                                f"{result.get('error', 'Unknown error')}"
                            )
                            stats["errors"].append({
                                "location_id": location['location_id'],
                                "village": location['village'],
                                "district": location['district'],
                                "error": result.get('error', 'Unknown error')
                            })
                            logger.error(error_msg)

                        # Rate limiting: sleep between API calls
                        if not dry_run:
                            time.sleep(API_CALL_DELAY)

                    except Exception as e:
                        stats["failed"] += 1
                        error_msg = f"Unexpected error processing location {location.get('location_id')}: {str(e)}"
                        stats["errors"].append({
                            "location_id": location.get('location_id'),
                            "village": location.get('village'),
                            "error": str(e)
                        })
                        logger.error(error_msg, exc_info=True)

                # Log batch progress
                logger.info(
                    f"Batch {batch_start + 1}-{batch_end} completed: "
                    f"Success: {stats['successful']}, Failed: {stats['failed']}, "
                    f"Skipped: {stats['skipped']}, Duplicates: {stats['duplicates']}"
                )

            # Final summary
            logger.info("=" * 80)
            logger.info("Polygon fetching process completed")
            logger.info(f"Total processed: {stats['total_processed']}")
            logger.info(f"Successful: {stats['successful']}")
            logger.info(f"Failed: {stats['failed']}")
            logger.info(f"Skipped: {stats['skipped']}")
            logger.info(f"Duplicates: {stats['duplicates']}")
            logger.info("=" * 80)

            return stats

        except Exception as e:
            logger.error(f"Critical error in fetch_all_polygons: {str(e)}", exc_info=True)
            raise

    def _get_unique_locations(self, skip_existing: bool = True) -> List[Dict[str, Any]]:
        """
        Get unique location records from operations.locations.
        
        Args:
            skip_existing: If True, skip locations that already have polygons
            
        Returns:
            List of unique location dictionaries
        """
        try:
            conn = get_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            
            # Build query to get unique locations
            if skip_existing:
                query = """
                    SELECT DISTINCT ON (LOWER(TRIM(village)), LOWER(TRIM(mandal)), 
                                       LOWER(TRIM(district)), LOWER(TRIM(state)))
                        l.location_id,
                        TRIM(l.village) as village,
                        TRIM(l.mandal) as mandal,
                        TRIM(l.district) as district,
                        TRIM(l.state) as state,
                        l.latitude,
                        l.longitude
                    FROM operations.locations l
                    WHERE l.village IS NOT NULL 
                      AND l.district IS NOT NULL
                      AND TRIM(l.village) != ''
                      AND TRIM(l.district) != ''
                      AND NOT EXISTS (
                          SELECT 1 
                          FROM operations.location_polygons lp
                          WHERE lp.location_id = l.location_id
                      )
                    ORDER BY LOWER(TRIM(village)), LOWER(TRIM(mandal)), 
                             LOWER(TRIM(district)), LOWER(TRIM(state)),
                             l.location_id
                """
            else:
                query = """
                    SELECT DISTINCT ON (LOWER(TRIM(village)), LOWER(TRIM(mandal)), 
                                       LOWER(TRIM(district)), LOWER(TRIM(state)))
                        l.location_id,
                        TRIM(l.village) as village,
                        TRIM(l.mandal) as mandal,
                        TRIM(l.district) as district,
                        TRIM(l.state) as state,
                        l.latitude,
                        l.longitude
                    FROM operations.locations l
                    WHERE l.village IS NOT NULL 
                      AND l.district IS NOT NULL
                      AND TRIM(l.village) != ''
                      AND TRIM(l.district) != ''
                    ORDER BY LOWER(TRIM(village)), LOWER(TRIM(mandal)), 
                             LOWER(TRIM(district)), LOWER(TRIM(state)),
                             l.location_id
                """
            
            cur.execute(query)
            locations = cur.fetchall()
            
            # Convert to list of dictionaries
            result = []
            for loc in locations:
                result.append({
                    'location_id': loc['location_id'],
                    'village': loc['village'].lower().strip() if loc['village'] else None,
                    'mandal': loc['mandal'].lower().strip() if loc['mandal'] else None,
                    'district': loc['district'].lower().strip() if loc['district'] else None,
                    'state': loc['state'].lower().strip() if loc['state'] else None,
                    'latitude': loc['latitude'],
                    'longitude': loc['longitude']
                })
            
            cur.close()
            release_connection(conn)
            
            logger.info(f"Retrieved {len(result)} unique locations")
            return result
            
        except Exception as e:
            logger.error(f"Error fetching unique locations: {str(e)}", exc_info=True)
            if 'cur' in locals():
                cur.close()
            if 'conn' in locals():
                release_connection(conn)
            raise

    def _fetch_polygon_from_api(self, location: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch polygon from Bhuvan API with retry logic.
        
        Args:
            location: Dictionary with village, mandal, district, state
            
        Returns:
            Dictionary with 'success' boolean and either 'geometry' or 'error'
        """
        # Prepare API request parameters
        params = {
            "village": location['village'],
            "district": location['district']
        }
        
        # Add optional parameters if available
        if location.get('mandal'):
            params["mandal"] = location['mandal']
        if location.get('state'):
            params["state"] = location['state']
        
        # Add access token as query parameter (common pattern for geospatial APIs)
        params["access_token"] = self.access_token
        
        # Also try as header if query param doesn't work
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        url = f"{BHUVAN_API_BASE_URL}{BHUVAN_VILLAGE_POLYGON_ENDPOINT}"
        
        # Retry logic
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                logger.debug(
                    f"API request attempt {attempt + 1}/{MAX_RETRIES + 1} for "
                    f"{location['village']}, {location['district']}"
                )
                
                # Try with query parameters first
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=30
                )
                
                # Handle HTTP errors
                if response.status_code == 200:
                    return self._parse_api_response(response, location)
                elif response.status_code == 401:
                    error_msg = "Authentication failed - invalid access token"
                    logger.error(error_msg)
                    return {"success": False, "error": error_msg}
                elif response.status_code == 404:
                    error_msg = f"Village not found: {location['village']}, {location['district']}"
                    logger.warning(error_msg)
                    return {"success": False, "error": error_msg}
                elif response.status_code == 429:
                    # Rate limit exceeded
                    wait_time = RETRY_DELAY * (2 ** attempt)
                    logger.warning(f"Rate limit exceeded, waiting {wait_time}s before retry")
                    time.sleep(wait_time)
                    last_error = f"Rate limit exceeded (HTTP 429)"
                    continue
                else:
                    error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(f"API error: {error_msg}")
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY * (attempt + 1))
                        last_error = error_msg
                        continue
                    return {"success": False, "error": error_msg}
                    
            except requests.exceptions.Timeout:
                error_msg = "Request timeout"
                logger.warning(f"Timeout on attempt {attempt + 1}: {error_msg}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    last_error = error_msg
                    continue
                return {"success": False, "error": error_msg}
                
            except requests.exceptions.RequestException as e:
                error_msg = f"Request error: {str(e)}"
                logger.warning(f"Request error on attempt {attempt + 1}: {error_msg}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    last_error = error_msg
                    continue
                return {"success": False, "error": error_msg}
                
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                logger.error(f"Unexpected error on attempt {attempt + 1}: {error_msg}", exc_info=True)
                return {"success": False, "error": error_msg}
        
        # All retries exhausted
        return {"success": False, "error": last_error or "Max retries exceeded"}

    def _parse_api_response(
        self,
        response: requests.Response,
        location: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Parse API response and extract polygon geometry.
        
        Args:
            response: HTTP response object
            location: Location dictionary for logging
            
        Returns:
            Dictionary with 'success' boolean and either 'geometry' or 'error'
        """
        try:
            # Try to parse JSON response
            try:
                data = response.json()
            except json.JSONDecodeError:
                # Try parsing as text/plain or other formats
                text_content = response.text.strip()
                if not text_content:
                    return {"success": False, "error": "Empty response from API"}
                
                # Try to parse as GeoJSON directly
                try:
                    data = json.loads(text_content)
                except json.JSONDecodeError:
                    return {"success": False, "error": f"Invalid JSON response: {text_content[:200]}"}
            
            # Extract geometry from response
            # Common response formats:
            # 1. Direct GeoJSON Feature: {"type": "Feature", "geometry": {...}}
            # 2. GeoJSON FeatureCollection: {"type": "FeatureCollection", "features": [...]}
            # 3. Wrapped response: {"data": {...}, "geometry": {...}}
            # 4. Direct geometry: {"type": "Polygon", "coordinates": [...]}
            
            geometry = None
            
            # Check for direct geometry
            if data.get("type") == "Polygon" or data.get("type") == "MultiPolygon":
                geometry = data
            # Check for Feature
            elif data.get("type") == "Feature" and "geometry" in data:
                geometry = data["geometry"]
            # Check for FeatureCollection
            elif data.get("type") == "FeatureCollection" and "features" in data:
                features = data["features"]
                if features and len(features) > 0:
                    geometry = features[0].get("geometry")
                    if not geometry and "geometry" in features[0]:
                        geometry = features[0]["geometry"]
            # Check for wrapped response
            elif "geometry" in data:
                geometry = data["geometry"]
            elif "data" in data and isinstance(data["data"], dict):
                if "geometry" in data["data"]:
                    geometry = data["data"]["geometry"]
                elif data["data"].get("type") in ["Polygon", "MultiPolygon"]:
                    geometry = data["data"]
            
            if not geometry:
                return {
                    "success": False,
                    "error": f"Could not extract geometry from response. Response keys: {list(data.keys())}"
                }
            
            # Validate geometry
            validation_result = self._validate_geometry(geometry)
            if not validation_result["valid"]:
                return {"success": False, "error": validation_result["error"]}
            
            # Normalize to GeoJSON format
            normalized_geometry = self._normalize_to_geojson(geometry)
            
            return {
                "success": True,
                "geometry": normalized_geometry
            }
            
        except Exception as e:
            logger.error(f"Error parsing API response: {str(e)}", exc_info=True)
            return {"success": False, "error": f"Parse error: {str(e)}"}

    def _validate_geometry(self, geometry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate polygon geometry structure and coordinates.
        
        Args:
            geometry: Geometry dictionary (GeoJSON format)
            
        Returns:
            Dictionary with 'valid' boolean and optional 'error' message
        """
        try:
            # Check for required fields
            if not isinstance(geometry, dict):
                return {"valid": False, "error": "Geometry must be a dictionary"}
            
            geom_type = geometry.get("type")
            if geom_type not in ["Polygon", "MultiPolygon"]:
                return {
                    "valid": False,
                    "error": f"Unsupported geometry type: {geom_type}. Expected Polygon or MultiPolygon"
                }
            
            coordinates = geometry.get("coordinates")
            if not coordinates:
                return {"valid": False, "error": "Missing coordinates in geometry"}
            
            if not isinstance(coordinates, list):
                return {"valid": False, "error": "Coordinates must be a list"}
            
            # Validate Polygon coordinates
            if geom_type == "Polygon":
                if not isinstance(coordinates[0], list):
                    return {"valid": False, "error": "Polygon coordinates must be nested lists"}
                
                # Check if polygon has at least 4 points (closed ring)
                if len(coordinates[0]) < 4:
                    return {"valid": False, "error": "Polygon must have at least 4 points"}
                
                # Validate coordinate pairs
                for coord in coordinates[0]:
                    if not isinstance(coord, list) or len(coord) < 2:
                        return {"valid": False, "error": "Invalid coordinate format"}
                    
                    lon, lat = coord[0], coord[1]
                    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
                        return {"valid": False, "error": "Coordinates must be numbers"}
                    
                    if not (-180 <= lon <= 180):
                        return {"valid": False, "error": f"Longitude out of range: {lon}"}
                    if not (-90 <= lat <= 90):
                        return {"valid": False, "error": f"Latitude out of range: {lat}"}
            
            # Validate MultiPolygon coordinates
            elif geom_type == "MultiPolygon":
                if not isinstance(coordinates[0], list):
                    return {"valid": False, "error": "MultiPolygon coordinates must be nested lists"}
                
                for polygon in coordinates:
                    if not isinstance(polygon, list) or len(polygon) == 0:
                        return {"valid": False, "error": "Invalid MultiPolygon structure"}
                    if len(polygon[0]) < 4:
                        return {"valid": False, "error": "MultiPolygon ring must have at least 4 points"}
            
            return {"valid": True}
            
        except Exception as e:
            return {"valid": False, "error": f"Validation error: {str(e)}"}

    def _normalize_to_geojson(self, geometry: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize geometry to standard GeoJSON format.
        
        Args:
            geometry: Geometry dictionary
            
        Returns:
            Normalized GeoJSON geometry dictionary
        """
        # Ensure it's a proper GeoJSON geometry object
        normalized = {
            "type": geometry.get("type"),
            "coordinates": geometry.get("coordinates")
        }
        
        # Add CRS if present (preserve it)
        if "crs" in geometry:
            normalized["crs"] = geometry["crs"]
        
        return normalized

    def _polygon_exists(self, location_id: str) -> bool:
        """
        Check if a polygon already exists for the given location_id.
        
        Args:
            location_id: Location ID to check
            
        Returns:
            True if polygon exists, False otherwise
        """
        try:
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT 1 
                FROM operations.location_polygons
                WHERE location_id = %s
                LIMIT 1
            """, (location_id,))
            
            exists = cur.fetchone() is not None
            
            cur.close()
            release_connection(conn)
            
            return exists
            
        except Exception as e:
            logger.error(f"Error checking polygon existence: {str(e)}")
            if 'cur' in locals():
                cur.close()
            if 'conn' in locals():
                release_connection(conn)
            return False

    def _store_polygon(self, location: Dict[str, Any], geometry: Dict[str, Any]) -> None:
        """
        Store polygon geometry in the database.
        
        Args:
            location: Location dictionary with location_id, village, mandal, district, state
            geometry: GeoJSON geometry dictionary
        """
        try:
            conn = get_connection()
            cur = conn.cursor()
            
            # Insert polygon record
            # Using PostGIS ST_GeomFromGeoJSON for geometry storage
            # ST_GeomFromGeoJSON handles both Polygon and MultiPolygon types
            insert_query = """
                INSERT INTO operations.location_polygons (
                    location_id,
                    village,
                    mandal,
                    district,
                    state,
                    polygon_geom,
                    source,
                    created_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                    %s,
                    %s
                )
                ON CONFLICT (location_id) DO NOTHING
            """
            
            # Convert geometry to JSON string for PostGIS
            geometry_json = json.dumps(geometry)
            
            cur.execute(insert_query, (
                location['location_id'],
                location['village'],
                location.get('mandal'),
                location['district'],
                location.get('state'),
                geometry_json,
                'bhuvan',
                datetime.utcnow()
            ))
            
            conn.commit()
            cur.close()
            release_connection(conn)
            
            logger.debug(f"Stored polygon for location_id: {location['location_id']}")
            
        except psycopg2.IntegrityError as e:
            # Duplicate key error - already exists
            conn.rollback()
            logger.warning(f"Polygon already exists for location_id: {location['location_id']}")
            if 'cur' in locals():
                cur.close()
            release_connection(conn)
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Error storing polygon for location_id {location['location_id']}: {str(e)}", exc_info=True)
            if 'cur' in locals():
                cur.close()
            release_connection(conn)
            raise

    def close(self):
        """Close the requests session."""
        if self.session:
            self.session.close()

