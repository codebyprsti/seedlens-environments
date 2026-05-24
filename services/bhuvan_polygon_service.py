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
from core.db import get_connection, release_connection, DB_URL
from services.overpass_client import fetch_village_boundary

logger = logging.getLogger(__name__)

# --- Temporary debug (production issue: rows not visible after sync) ---
# Set DEBUG_POLYGON_PERSISTENCE = False after verification.
# Logs to check: Connection context (database, schema, search_path, DSN masked);
# BEFORE INSERT / BEFORE BATCH INSERT (location_id(s), _polygon_exists=False);
# Actual INSERT SQL (params); AFTER COMMIT + COUNT(*) on same connection.
DEBUG_POLYGON_PERSISTENCE = True  # Set False after verification
# Read path: log current_database(), current_schema(), search_path when reading location_polygons.
DEBUG_READ_PATH = True  # Set False after verification


def _masked_dsn() -> str:
    """Build DSN with masked password for debug logs."""
    p = {**DB_URL, "password": "***" if DB_URL.get("password") else ""}
    return f"postgresql://{p.get('user')}:{p.get('password')}@{p.get('host')}:{p.get('port')}/{p.get('dbname')}"


def _log_connection_context(cur, read_path: bool = False) -> None:
    """Log current database, schema, search_path and masked DSN (same connection)."""
    if read_path:
        if not DEBUG_READ_PATH:
            return
        prefix = "[DEBUG READ PATH] "
    else:
        if not DEBUG_POLYGON_PERSISTENCE:
            return
        prefix = "[DEBUG] "
    try:
        cur.execute("""
            SELECT current_database(), current_schema(),
                   current_setting('search_path')
        """)
        row = cur.fetchone()
        db_name = row[0] if row else "?"
        schema = row[1] if row and len(row) > 1 else "?"
        search_path = row[2] if row and len(row) > 2 else "?"
        logger.info(
            "%sConnection context: database=%s schema=%s search_path=%s",
            prefix, db_name, schema, search_path
        )
        logger.info("%sConnection DSN (masked): %s", prefix, _masked_dsn())
    except Exception as e:
        logger.warning("%sCould not get connection context: %s", prefix, e)

# Bhuvan API Configuration
BHUVAN_API_BASE_URL = "https://bhuvan-app1.nrsc.gov.in/api"
BHUVAN_VILLAGE_POLYGON_ENDPOINT = "/village/boundary"
# Coordinate-based endpoint if available (fallback when name lookup fails)
BHUVAN_BOUNDARY_BY_COORDS_ENDPOINT = "/boundary/bycoords"
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

    @staticmethod
    def _normalize_str(s: Optional[str]) -> Optional[str]:
        """Strip whitespace, collapse double spaces, title case."""
        if s is None:
            return None
        t = " ".join(str(s).strip().split())
        return t.title() if t else None

    def _normalize_location_for_api(self, location: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy with normalized village, mandal, district, state. Log request payload."""
        out = dict(location)
        for key in ("village", "mandal", "district", "state"):
            if key in out and out[key] is not None:
                out[key] = self._normalize_str(out[key])
        logger.info(
            "[API request] location_id=%s village=%s mandal=%s district=%s state=%s lat=%s lon=%s",
            out.get("location_id"),
            out.get("village"),
            out.get("mandal"),
            out.get("district"),
            out.get("state"),
            out.get("latitude"),
            out.get("longitude"),
        )
        return out

    def get_locations_for_polygon_sync(
        self,
        skip_existing: bool = True,
        location_ids: Optional[List[str]] = None,
        fetch_all: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get location records for polygon sync.
        If fetch_all=True, returns ALL rows from operations.locations (no DISTINCT).
        Otherwise uses _get_unique_locations (DISTINCT, optional skip_existing).
        """
        if fetch_all:
            return self._get_all_locations(location_ids=location_ids, skip_existing=skip_existing)
        return self._get_unique_locations(skip_existing=skip_existing, location_ids=location_ids)

    def _get_all_locations(
        self,
        location_ids: Optional[List[str]] = None,
        skip_existing: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Fetch ALL locations: SELECT location_id, village, mandal, district, state, latitude, longitude
        FROM operations.locations. Optional filter by location_ids; optional skip where polygons exist.
        """
        try:
            conn = get_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            _log_connection_context(cur, read_path=True)

            location_filter = ""
            if location_ids:
                placeholders = ",".join("%s" for _ in location_ids)
                location_filter = f" AND l.location_id IN ({placeholders})"
            skip_clause = ""
            if skip_existing:
                skip_clause = """
                    AND NOT EXISTS (
                        SELECT 1 FROM operations.location_polygons lp
                        WHERE lp.location_id = l.location_id
                    )
                """
            query = f"""
                SELECT l.location_id, l.village, l.mandal, l.district, l.state, l.latitude, l.longitude
                FROM operations.locations l
                WHERE 1=1
                {location_filter}
                {skip_clause}
                ORDER BY l.location_id
            """
            if location_ids:
                cur.execute(query, tuple(location_ids))
            else:
                cur.execute(query)
            rows = cur.fetchall()
            result = []
            for r in rows:
                result.append({
                    "location_id": r["location_id"],
                    "village": r["village"].strip() if r["village"] else None,
                    "mandal": r["mandal"].strip() if r["mandal"] else None,
                    "district": r["district"].strip() if r["district"] else None,
                    "state": r["state"].strip() if r["state"] else None,
                    "latitude": r["latitude"],
                    "longitude": r["longitude"],
                })
            cur.close()
            release_connection(conn)
            logger.info("Retrieved %s locations (fetch_all=True)", len(result))
            return result
        except Exception as e:
            logger.error("Error fetching all locations: %s", e, exc_info=True)
            if "cur" in locals():
                cur.close()
            if "conn" in locals():
                release_connection(conn)
            raise

    def _get_unique_locations(
        self,
        skip_existing: bool = True,
        location_ids: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get unique location records from operations.locations.
        
        Args:
            skip_existing: If True, skip locations that already have polygons
            location_ids: Optional list of location_id to restrict to
            
        Returns:
            List of unique location dictionaries
        """
        try:
            conn = get_connection()
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            _log_connection_context(cur, read_path=True)

            # Optional filter by location_ids
            location_filter = ""
            if location_ids:
                placeholders = ",".join("%s" for _ in location_ids)
                location_filter = f" AND l.location_id IN ({placeholders})"
            
            # Build query to get unique locations
            if skip_existing:
                query = f"""
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
                      {location_filter}
                    ORDER BY LOWER(TRIM(village)), LOWER(TRIM(mandal)), 
                             LOWER(TRIM(district)), LOWER(TRIM(state)),
                             l.location_id
                """
            else:
                query = f"""
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
                      {location_filter}
                    ORDER BY LOWER(TRIM(village)), LOWER(TRIM(mandal)), 
                             LOWER(TRIM(district)), LOWER(TRIM(state)),
                             l.location_id
                """
            
            if location_ids:
                cur.execute(query, tuple(location_ids))
            else:
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

    def _fetch_polygon_from_overpass(self, location: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch polygon from OpenStreetMap Overpass API (administrative boundary).
        Uses village + district + state. Returns same shape as _fetch_polygon_from_api.
        """
        loc = self._normalize_location_for_api(location)
        village = (loc.get("village") or "").strip()
        district = (loc.get("district") or "").strip()
        state = (loc.get("state") or "").strip()
        if not village or not state:
            return {
                "success": False,
                "error": "village and state required for Overpass",
                "api_404": False,
                "api_empty_response": False,
            }
        logger.info(
            "[Overpass] request location_id=%s village=%s district=%s state=%s",
            location.get("location_id"), village, district, state,
        )
        result = fetch_village_boundary(village, state, district=district or None, use_area=True)
        if not result.get("success"):
            if result.get("api_404"):
                logger.warning("[Overpass] No boundary for %s, %s", village, state)
            return result
        geometry = result["geometry"]
        validation = self._validate_geometry(geometry)
        if not validation["valid"]:
            return {
                "success": False,
                "error": validation["error"],
                "api_404": False,
                "api_empty_response": False,
            }
        return {
            "success": True,
            "geometry": self._normalize_to_geojson(geometry),
            "api_404": False,
            "api_empty_response": False,
        }

    def _fetch_polygon_from_api(self, location: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fetch polygon from Bhuvan API. Normalizes location, then tries strategies in order:
        A) Coordinate-based (if lat/lon exist), B) village+mandal+district+state, C) village+district+state.
        Returns dict with success, geometry or error, api_404, api_empty_response.
        """
        loc = self._normalize_location_for_api(location)

        if not loc.get("village") or not loc.get("district"):
            return {
                "success": False,
                "error": "village and district required",
                "api_404": False,
                "api_empty_response": False,
            }

        # Strategy A: coordinate-based if available
        lat, lon = loc.get("latitude"), loc.get("longitude")
        if lat is not None and lon is not None:
            try:
                result = self._try_coordinate_api(lat, lon, loc)
                if result.get("success"):
                    return result
                if result.get("api_404") or result.get("api_empty_response"):
                    # Fall through to name-based
                    pass
            except Exception as e:
                logger.debug("Coordinate API failed: %s, trying name-based", e)

        # Strategy B: village + mandal + district + state
        result = self._try_name_based_api(loc, include_mandal=True)
        if result.get("success"):
            return result

        # Strategy C: village + district + state (no mandal)
        if loc.get("mandal"):
            result = self._try_name_based_api(loc, include_mandal=False)
            if result.get("success"):
                return result

        # Return last failure (with api_404 / api_empty_response preserved)
        return result

    def _try_coordinate_api(self, lat: float, lon: float, location: Dict[str, Any]) -> Dict[str, Any]:
        """Try coordinate-based boundary endpoint. Returns result with api_404, api_empty_response."""
        url = f"{BHUVAN_API_BASE_URL}{BHUVAN_BOUNDARY_BY_COORDS_ENDPOINT}"
        params = {"lat": lat, "lon": lon, "access_token": self.access_token}
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        logger.info("[API request] strategy=coords payload=%s", params)
        try:
            response = self.session.get(url, params=params, headers=headers, timeout=30)
            if response.status_code == 404:
                logger.warning("[API] 404 for coords lat=%s lon=%s", lat, lon)
                return {
                    "success": False,
                    "error": "Boundary not found for coordinates",
                    "api_404": True,
                    "api_empty_response": False,
                }
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}",
                    "api_404": False,
                    "api_empty_response": False,
                }
            out = self._parse_api_response(response, location)
            out.setdefault("api_404", False)
            out.setdefault("api_empty_response", False)
            return out
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "api_404": False,
                "api_empty_response": False,
            }

    def _try_name_based_api(self, location: Dict[str, Any], include_mandal: bool) -> Dict[str, Any]:
        """Call village boundary API with name params. Returns result with api_404, api_empty_response."""
        params = {
            "village": location["village"],
            "district": location["district"],
            "access_token": self.access_token,
        }
        if include_mandal and location.get("mandal"):
            params["mandal"] = location["mandal"]
        if location.get("state"):
            params["state"] = location["state"]
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        logger.info("[API request] strategy=name include_mandal=%s payload=%s", include_mandal, params)
        url = f"{BHUVAN_API_BASE_URL}{BHUVAN_VILLAGE_POLYGON_ENDPOINT}"
        last_error = None
        last_api_404 = False
        last_api_empty = False
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=30)
                if response.status_code == 200:
                    out = self._parse_api_response(response, location)
                    out.setdefault("api_404", False)
                    out.setdefault("api_empty_response", False)
                    return out
                if response.status_code == 404:
                    last_api_404 = True
                    last_error = f"Village not found: {location.get('village')}, {location.get('district')}"
                    logger.warning("[API] 404 %s", last_error)
                    return {
                        "success": False,
                        "error": last_error,
                        "api_404": True,
                        "api_empty_response": False,
                    }
                if response.status_code == 429:
                    wait_time = RETRY_DELAY * (2 ** attempt)
                    time.sleep(wait_time)
                    last_error = "Rate limit exceeded (HTTP 429)"
                    continue
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return {
                    "success": False,
                    "error": last_error,
                    "api_404": False,
                    "api_empty_response": False,
                }
            except requests.exceptions.Timeout:
                last_error = "Request timeout"
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return {"success": False, "error": last_error, "api_404": False, "api_empty_response": False}
            except requests.exceptions.RequestException as e:
                last_error = str(e)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return {"success": False, "error": last_error, "api_404": False, "api_empty_response": False}
        return {
            "success": False,
            "error": last_error or "Max retries exceeded",
            "api_404": last_api_404,
            "api_empty_response": last_api_empty,
        }

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
                    return {
                        "success": False,
                        "error": "Empty response from API",
                        "api_404": False,
                        "api_empty_response": True,
                    }
                
                # Try to parse as GeoJSON directly
                try:
                    data = json.loads(text_content)
                except json.JSONDecodeError:
                    return {
                        "success": False,
                        "error": f"Invalid JSON response: {text_content[:200]}",
                        "api_404": False,
                        "api_empty_response": False,
                    }
            
            # Extract geometry from response
            # Common response formats:
            # 1. Direct GeoJSON Feature: {"type": "Feature", "geometry": {...}}
            # 2. GeoJSON FeatureCollection: {"type": "FeatureCollection", "features": [...]}
            # 3. Wrapped response: {"data": {...}, "geometry": {...}}
            # 4. Direct geometry: {"type": "Polygon", "coordinates": [...]}
            
            geometry = None
            
            # FeatureCollection: pass whole structure so _geometry_to_polygon_list iterates all features
            if data.get("type") == "FeatureCollection" and "features" in data:
                if not data["features"]:
                    return {
                        "success": False,
                        "error": "FeatureCollection has no features",
                        "api_404": False,
                        "api_empty_response": True,
                    }
                geometry = data
            # Direct geometry
            elif data.get("type") == "Polygon" or data.get("type") == "MultiPolygon":
                geometry = data
            elif data.get("type") == "Feature" and "geometry" in data:
                geometry = data["geometry"]
            elif "geometry" in data:
                geometry = data["geometry"]
            elif "data" in data and isinstance(data["data"], dict):
                if "geometry" in data["data"]:
                    geometry = data["data"]["geometry"]
                elif data["data"].get("type") in ["Polygon", "MultiPolygon"]:
                    geometry = data["data"]
                else:
                    geometry = None
            else:
                geometry = None

            if not geometry:
                return {
                    "success": False,
                    "error": f"Could not extract geometry. Response keys: {list(data.keys())}",
                    "api_404": False,
                    "api_empty_response": False,
                }

            # Validate (for single geometry; FeatureCollection validated per-feature in _geometry_to_polygon_list)
            if geometry.get("type") in ("Polygon", "MultiPolygon"):
                validation_result = self._validate_geometry(geometry)
                if not validation_result["valid"]:
                    return {
                        "success": False,
                        "error": validation_result["error"],
                        "api_404": False,
                        "api_empty_response": False,
                    }
            elif geometry.get("type") == "FeatureCollection":
                for i, feat in enumerate(geometry.get("features", [])):
                    g = feat.get("geometry") if isinstance(feat, dict) else None
                    if g:
                        vr = self._validate_geometry(g)
                        if not vr["valid"]:
                            return {
                                "success": False,
                                "error": f"Feature {i}: {vr['error']}",
                                "api_404": False,
                                "api_empty_response": False,
                            }

            if geometry.get("type") == "FeatureCollection":
                normalized = geometry
            else:
                normalized = self._normalize_to_geojson(geometry)

            return {
                "success": True,
                "geometry": normalized,
                "api_404": False,
                "api_empty_response": False,
            }
            
        except Exception as e:
            logger.error("Error parsing API response: %s", e, exc_info=True)
            return {
                "success": False,
                "error": f"Parse error: {str(e)}",
                "api_404": False,
                "api_empty_response": False,
            }

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

    def _geometry_to_polygon_list(self, geometry: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Normalize API geometry to a list of Polygon GeoJSON dicts.
        - Polygon -> [geometry]
        - MultiPolygon -> split into one Polygon per part
        - FeatureCollection -> iterate features, collect all Polygon(s) from each
        - Feature -> extract geometry and recurse
        """
        if not geometry or not isinstance(geometry, dict):
            return []
        geom_type = geometry.get("type")
        if geom_type == "Polygon":
            return [{"type": "Polygon", "coordinates": geometry.get("coordinates", [])}]
        if geom_type == "MultiPolygon":
            out = []
            for ring_list in geometry.get("coordinates", []):
                out.append({"type": "Polygon", "coordinates": ring_list})
            return out
        if geom_type == "FeatureCollection":
            out = []
            for feat in geometry.get("features", []):
                g = feat.get("geometry") if isinstance(feat, dict) else None
                if g:
                    out.extend(self._geometry_to_polygon_list(g))
            return out
        if geom_type == "Feature":
            g = geometry.get("geometry")
            return self._geometry_to_polygon_list(g) if g else []
        return []

    @staticmethod
    def _polygon_to_multipolygon_geojson(polygon_geom: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a single Polygon GeoJSON to MultiPolygon for storage.
        Column polygon_geom is GEOMETRY(MULTIPOLYGON, 4326); PostGIS expects MultiPolygon.
        """
        coords = polygon_geom.get("coordinates", [])
        return {"type": "MultiPolygon", "coordinates": [coords]}

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
            _log_connection_context(cur, read_path=True)

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

    def _store_polygon(
        self,
        location: Dict[str, Any],
        geometry: Dict[str, Any],
        source: str = "bhuvan",
    ) -> Dict[str, Any]:
        """
        Store polygon(s) for one location. Normalizes MultiPolygon/FeatureCollection
        into individual Polygon rows. Optionally deletes existing rows for this location_id
        then inserts each with polygon_index. Transaction-controlled; no ON CONFLICT.
        source: 'bhuvan' | 'overpass' for operations.location_polygons.source.
        Returns {"location_id": str, "polygons_inserted": int}.
        """
        polygons = self._geometry_to_polygon_list(geometry)
        location_id = location["location_id"]
        if not polygons:
            logger.warning("No polygon(s) to store for location_id=%s", location_id)
            return {"location_id": location_id, "polygons_inserted": 0}

        conn = None
        cur = None
        try:
            conn = get_connection()
            cur = conn.cursor()
            try:
                cur.execute("SET search_path TO public, operations")
            except Exception:
                pass

            if DEBUG_POLYGON_PERSISTENCE:
                _log_connection_context(cur)
                logger.info(
                    "[DEBUG] BEFORE INSERT: location_id=%s polygons_count=%s",
                    location_id, len(polygons),
                )

            # Optional: delete existing polygons for this location before insert
            cur.execute(
                "DELETE FROM operations.location_polygons WHERE location_id = %s",
                (location_id,),
            )
            deleted = cur.rowcount

            # polygon_geom is GEOMETRY(MULTIPOLYGON, 4326); polygon_geojson is JSONB
            insert_sql = """
                INSERT INTO operations.location_polygons (
                    location_id,
                    polygon_index,
                    village,
                    mandal,
                    district,
                    state,
                    polygon_geom,
                    polygon_geojson,
                    source,
                    created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    ST_SetSRID(ST_GeomFromGeoJSON(%s::text), 4326),
                    %s::jsonb,
                    %s,
                    NOW()
                )
            """
            for idx, geom in enumerate(polygons):
                multipoly = self._polygon_to_multipolygon_geojson(geom)
                geom_json = json.dumps(multipoly)
                cur.execute(
                    insert_sql,
                    (
                        location_id,
                        idx,
                        location.get("village"),
                        location.get("mandal"),
                        location.get("district"),
                        location.get("state"),
                        geom_json,
                        geom_json,
                        source,
                    ),
                )
            conn.commit()
            n = len(polygons)
            if DEBUG_POLYGON_PERSISTENCE:
                cur.execute("SELECT COUNT(*) FROM operations.location_polygons")
                count_row = cur.fetchone()
                count_after = count_row[0] if count_row else None
                cur.execute(
                    "SELECT COUNT(*) FROM operations.location_polygons WHERE location_id = %s",
                    (location_id,),
                )
                count_for_loc_row = cur.fetchone()
                count_for_location = count_for_loc_row[0] if count_for_loc_row else None
                logger.info(
                    "[DEBUG] AFTER COMMIT: location_id=%s polygons_inserted=%s count_after=%s "
                    "count_for_location=%s DSN=%s",
                    location_id, n, count_after, count_for_location, _masked_dsn(),
                )
            logger.info(
                "Stored %s polygon(s) for location_id=%s (replaced %s previous)",
                n, location_id, deleted,
            )
            return {"location_id": location_id, "polygons_inserted": n}
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(
                "Error storing polygons for location_id=%s: %s",
                location_id, str(e),
                exc_info=True,
            )
            raise
        finally:
            if cur:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn:
                release_connection(conn)

    def store_polygons_batch(
        self,
        items: List[Tuple[Dict[str, Any], Dict[str, Any]]],
        source: str = "bhuvan",
    ) -> int:
        """
        Insert polygons for multiple locations in a single transaction.
        For each (location, geometry): delete existing rows for that location_id,
        then insert one row per Polygon (MultiPolygon/FeatureCollection split).
        No ON CONFLICT. source: 'bhuvan' | 'overpass'. Returns total polygons_inserted.
        """
        if not items:
            logger.debug("store_polygons_batch: 0 items, skipping")
            return 0

        # polygon_geom is GEOMETRY(MULTIPOLYGON, 4326); polygon_geojson is JSONB
        insert_sql = """
            INSERT INTO operations.location_polygons (
                location_id,
                polygon_index,
                village,
                mandal,
                district,
                state,
                polygon_geom,
                polygon_geojson,
                source,
                created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                ST_SetSRID(ST_GeomFromGeoJSON(%s::text), 4326),
                %s::jsonb,
                %s,
                NOW()
            )
        """
        conn = None
        cur = None
        total_inserted = 0
        try:
            conn = get_connection()
            cur = conn.cursor()
            try:
                cur.execute("SET search_path TO public, operations")
            except Exception:
                pass

            if DEBUG_POLYGON_PERSISTENCE:
                _log_connection_context(cur)
                logger.info(
                    "[DEBUG] BEFORE BATCH: location_ids=%s",
                    [loc["location_id"] for loc, _ in items],
                )

            for location, geometry in items:
                polygons = self._geometry_to_polygon_list(geometry)
                location_id = location["location_id"]
                if not polygons:
                    continue
                cur.execute(
                    "DELETE FROM operations.location_polygons WHERE location_id = %s",
                    (location_id,),
                )
                for idx, geom in enumerate(polygons):
                    multipoly = self._polygon_to_multipolygon_geojson(geom)
                    geom_json = json.dumps(multipoly)
                    cur.execute(
                        insert_sql,
                        (
                            location_id,
                            idx,
                            location.get("village"),
                            location.get("mandal"),
                            location.get("district"),
                            location.get("state"),
                            geom_json,
                            geom_json,
                            source,
                        ),
                    )
                    total_inserted += cur.rowcount
            conn.commit()

            if DEBUG_POLYGON_PERSISTENCE:
                cur.execute("SELECT COUNT(*) FROM operations.location_polygons")
                count_row = cur.fetchone()
                count_after = count_row[0] if count_row else None
                logger.info(
                    "[DEBUG] AFTER COMMIT: total_inserted=%s count_after=%s",
                    total_inserted, count_after,
                )
            logger.info(
                "Batch committed: locations=%s polygons_inserted=%s",
                len(items), total_inserted,
            )
            return total_inserted
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(
                "Batch polygon insert failed: %s",
                str(e),
                exc_info=True,
            )
            raise
        finally:
            if cur:
                cur.close()
            if conn:
                release_connection(conn)

    def debug_force_insert(self) -> dict:
        """
        Minimal isolated DB insert test: same pool, direct INSERT into
        operations.location_polygons, no _polygon_exists, API, validation, or orchestrator.
        Uses an existing location_id from operations.locations so FK is satisfied.
        Returns {"rowcount": int, "count_after": int, "location_id_used": str} and logs connection context.
        """
        conn = None
        cur = None
        rowcount = 0
        count_after = None
        location_id_used = None
        try:
            conn = get_connection()
            cur = conn.cursor()
            # Log connection context (database, schema, search_path, DSN)
            _log_connection_context(cur, read_path=False)
            # Ensure PostGIS is available (required for polygon_geom)
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
                conn.commit()
            except Exception as e:
                logger.warning("[DEBUG FORCE INSERT] PostGIS extension create (optional): %s", e)
                conn.rollback()
            try:
                cur.execute("SET search_path TO public, operations")
            except Exception:
                pass
            # Pick one existing location_id so FK is satisfied (no debug_test_id in locations)
            cur.execute(
                "SELECT location_id, village, mandal, district, state FROM operations.locations LIMIT 1"
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(
                    "operations.locations is empty; add a location first so the debug insert can satisfy FK"
                )
            location_id_used = row[0]
            village = row[1] or "debug"
            mandal = row[2] or "debug"
            district = row[3] or "debug"
            state = row[4] or "debug"
            logger.info(
                "[DEBUG FORCE INSERT] Using existing location_id=%s, executing INSERT into operations.location_polygons",
                location_id_used,
            )
            cur.execute(
                "DELETE FROM operations.location_polygons WHERE location_id = %s",
                (location_id_used,),
            )
            # polygon_geom: GEOMETRY(MULTIPOLYGON, 4326); polygon_geojson: JSONB
            minimal_polygon = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
            minimal_multipoly = BhuvanPolygonService._polygon_to_multipolygon_geojson(minimal_polygon)
            minimal_geom_json = json.dumps(minimal_multipoly)
            insert_sql = """
                INSERT INTO operations.location_polygons (
                    location_id, polygon_index, village, mandal, district, state,
                    polygon_geom, polygon_geojson, source, created_at
                ) VALUES (
                    %s, 0, %s, %s, %s, %s,
                    ST_SetSRID(ST_GeomFromGeoJSON(%s::text), 4326),
                    %s::jsonb,
                    'debug',
                    NOW()
                )
            """
            cur.execute(
                insert_sql,
                (location_id_used, village, mandal, district, state, minimal_geom_json, minimal_geom_json),
            )
            rowcount = cur.rowcount
            conn.commit()
            cur.execute("SELECT COUNT(*) FROM operations.location_polygons")
            count_row = cur.fetchone()
            count_after = count_row[0] if count_row else None
            logger.info(
                "[DEBUG FORCE INSERT] rowcount=%s, count_after=%s, location_id_used=%s",
                rowcount, count_after, location_id_used
            )
            return {
                "rowcount": rowcount,
                "count_after": count_after,
                "location_id_used": location_id_used,
            }
        except Exception as e:
            if conn:
                conn.rollback()
            logger.exception("[DEBUG FORCE INSERT] Failed: %s", e)
            raise
        finally:
            if cur:
                try:
                    cur.close()
                except Exception:
                    pass
            if conn:
                release_connection(conn)

    def close(self):
        """Close the requests session."""
        if self.session:
            self.session.close()

