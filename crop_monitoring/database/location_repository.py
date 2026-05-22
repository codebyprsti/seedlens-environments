"""
Field locations: resolve or create operations.field_locations.
location_id format: IND-<STATE_CODE>-<NUMBER> (e.g. IND-KA-600001).
Uniqueness is by CENTROID (latitude, longitude within tolerance). One location_id per field/polygon.
Village, mandal, district, state are metadata only; not used for lookup.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Numbering starts from 600000 per state
FIELD_LOCATION_START_NUMBER = 600000


# Match KML precision: 7 decimal places max (avoid float artifacts from geometry libs)
COORD_DECIMALS = 7

# Centroid tolerance for lookup: ~1 meter. One location_id per centroid.
CENTROID_TOLERANCE = 0.00001


def _round_coord(x: float, decimals: int = None) -> float:
    """Round lat/lon for consistent lookup and storage (7 decimals = KML precision)."""
    return round(float(x), decimals if decimals is not None else COORD_DECIMALS)


def get_field_location_id(
    db: Session,
    village: str,
    district: Optional[str],
    state: Optional[str],
    state_code: Optional[str],
    mandal: Optional[str],
    postalcode: Optional[str],
    latitude: float,
    longitude: float,
    extracted_village: Optional[str] = None,
) -> Optional[str]:
    """
    Lookup or create a row in operations.field_locations.
    Uniqueness is by CENTROID (lat, lon within tolerance). If found return location_id; else create new.
    Village/district/state are metadata for insert only; not used for lookup.
    Returns None only if village is empty or create failed.
    """
    if not village or not str(village).strip():
        return None

    v = str(village).strip()[:200]
    d = (district and str(district).strip())[:200] if district else None
    s = (state and str(state).strip())[:200] if state else None
    lat = _round_coord(latitude)
    lon = _round_coord(longitude)

    # Lookup: by centroid only (one location_id per field)
    row = db.execute(
        text("""
            SELECT location_id FROM operations.field_locations
            WHERE ABS(latitude - :lat) < :tol AND ABS(longitude - :lon) < :tol
            LIMIT 1
        """),
        {"lat": lat, "lon": lon, "tol": CENTROID_TOLERANCE},
    ).fetchone()

    if row:
        return row[0]

    # Create new: need state_code for ID (fallback to first 2 chars of state or "XX")
    sc = (state_code and str(state_code).strip())[:20] if state_code else None
    if not sc and s:
        sc = s[:2].upper() if len(s) >= 2 else "XX"
    if not sc:
        sc = "XX"

    # Next number for this state_code: MAX(location_id) WHERE state_code = sc, extract number, +1; min 600000
    max_row = db.execute(
        text("""
            SELECT MAX(location_id) FROM operations.field_locations
            WHERE state_code = :sc
        """),
        {"sc": sc},
    ).scalar()

    num = FIELD_LOCATION_START_NUMBER
    if max_row:
        match = re.search(r"\d+", str(max_row))
        if match:
            num = int(match.group()) + 1
    location_id = f"IND-{sc}-{num}"

    extracted = (extracted_village and str(extracted_village).strip())[:200] if extracted_village else None
    try:
        db.execute(
            text("""
                INSERT INTO operations.field_locations
                (location_id, extracted_village, village, district, state, state_code, mandal, postalcode, latitude, longitude)
                VALUES (:location_id, :extracted_village, :village, :district, :state, :state_code, :mandal, :postalcode, :latitude, :longitude)
            """),
            {
                "location_id": location_id,
                "extracted_village": extracted,
                "village": v,
                "district": d,
                "state": s,
                "state_code": sc,
                "mandal": (mandal and str(mandal).strip())[:200] if mandal else None,
                "postalcode": (postalcode and str(postalcode).strip())[:50] if postalcode else None,
                "latitude": lat,
                "longitude": lon,
            },
        )
        db.commit()
        logger.info(
            "New location created:\n  location_id: %s\n  village: %s\n  district: %s\n  state: %s",
            location_id, v, d, s,
        )
        return location_id
    except Exception as e:
        db.rollback()
        logger.exception("create field_location failed: %s", e)
        return None


def get_field_location_row_by_centroid(
    db: Session,
    latitude: float,
    longitude: float,
) -> Optional[dict[str, Any]]:
    """
    Full row for crop_indices: location_id, village, district, state, mandal, postcode.
    Match centroids robustly:
    - First try strict 7-decimal ROUND equality (fast path).
    - Fallback to tolerance-based nearest match within CENTROID_TOLERANCE to handle real-world
      centroid differences (KML parsing / float rounding / re-digitized polygons).

    When multiple rows exist within tolerance, returns the closest by squared distance in degrees.
    """
    lat = _round_coord(latitude)
    lon = _round_coord(longitude)

    # Fast path: exact match at 7 dp (historical behavior)
    row = db.execute(
        text("""
            SELECT location_id, village, district, state, mandal, postalcode
            FROM operations.field_locations
            WHERE ROUND(CAST(latitude AS NUMERIC), 7) = ROUND(CAST(:lat AS NUMERIC), 7)
              AND ROUND(CAST(longitude AS NUMERIC), 7) = ROUND(CAST(:lon AS NUMERIC), 7)
            LIMIT 1
        """),
        {"lat": lat, "lon": lon},
    ).fetchone()
    if not row:
        # Fallback: tolerance window + nearest neighbor (prevents false "No matching location" skips)
        row = db.execute(
            text("""
                SELECT location_id, village, district, state, mandal, postalcode
                FROM operations.field_locations
                WHERE ABS(latitude - :lat) < :tol
                  AND ABS(longitude - :lon) < :tol
                ORDER BY ((latitude - :lat) * (latitude - :lat) + (longitude - :lon) * (longitude - :lon)) ASC
                LIMIT 1
            """),
            {"lat": lat, "lon": lon, "tol": CENTROID_TOLERANCE},
        ).fetchone()
        if not row:
            return None
    return {
        "location_id": str(row[0]) if row[0] else None,
        "village": (row[1] or "").strip() or None,
        "district": (row[2] or "").strip() or None if row[2] is not None else None,
        "state": (row[3] or "").strip() or None if row[3] is not None else None,
        "mandal": (row[4] or "").strip() or None if row[4] is not None else None,
        "postcode": (row[5] or "").strip() or None if row[5] is not None else None,
    }


def insert_field_location_if_absent(
    db: Session,
    latitude: float,
    longitude: float,
    *,
    extracted_village: Optional[str] = None,
) -> Tuple[Optional[str], bool]:
    """
    Idempotent insert by centroid. No geocoding / Google API.
    village column = extracted_village or 'Unknown field' (NOT NULL constraint).
    Returns (location_id, inserted) where inserted is True if a new row was created.
    """
    lat = _round_coord(latitude)
    lon = _round_coord(longitude)
    existing = db.execute(
        text("""
            SELECT location_id FROM operations.field_locations
            WHERE ABS(latitude - :lat) < :tol AND ABS(longitude - :lon) < :tol
            LIMIT 1
        """),
        {"lat": lat, "lon": lon, "tol": CENTROID_TOLERANCE},
    ).fetchone()
    if existing:
        return (str(existing[0]), False)

    v_display = (extracted_village and str(extracted_village).strip()[:200]) or "Unknown field"
    exv = (extracted_village and str(extracted_village).strip()[:200]) if extracted_village else None
    sc = "XX"
    max_row = db.execute(
        text("""
            SELECT MAX(location_id) FROM operations.field_locations
            WHERE state_code = :sc
        """),
        {"sc": sc},
    ).scalar()
    num = FIELD_LOCATION_START_NUMBER
    if max_row:
        match = re.search(r"\d+", str(max_row))
        if match:
            num = int(match.group()) + 1
    location_id = f"IND-{sc}-{num}"
    try:
        db.execute(
            text("""
                INSERT INTO operations.field_locations
                (location_id, extracted_village, village, district, state, state_code, mandal, postalcode, latitude, longitude)
                VALUES (:location_id, :extracted_village, :village, :district, :state, :state_code, :mandal, :postalcode, :latitude, :longitude)
            """),
            {
                "location_id": location_id,
                "extracted_village": exv,
                "village": v_display,
                "district": None,
                "state": None,
                "state_code": sc,
                "mandal": None,
                "postalcode": None,
                "latitude": lat,
                "longitude": lon,
            },
        )
        db.commit()
        logger.info("New location inserted: %s at (%.7f, %.7f)", location_id, lat, lon)
        return (location_id, True)
    except Exception as e:
        db.rollback()
        logger.exception("insert_field_location_if_absent failed: %s", e)
        return (None, False)


def get_field_location_by_centroid(
    db: Session,
    latitude: float,
    longitude: float,
) -> Optional[Tuple[str, str]]:
    """
    Lookup location_id and village in operations.field_locations by centroid coordinates.
    Uses tolerance 0.00001 (~1 m) to avoid floating-point mismatch.
    Returns (location_id, village) or None if no match.
    """
    lat = _round_coord(latitude)
    lon = _round_coord(longitude)
    row = db.execute(
        text("""
            SELECT location_id, village
            FROM operations.field_locations
            WHERE ABS(latitude - :lat) < :tol AND ABS(longitude - :lon) < :tol
            LIMIT 1
        """),
        {"lat": lat, "lon": lon, "tol": CENTROID_TOLERANCE},
    ).fetchone()
    if row:
        return (row[0], (row[1] or "").strip() or "")
    return None
