"""
Resolve operations.field_locations by centroid: use DB match first; Google geocode + insert only if missing.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from sqlalchemy.orm import Session

from crop_monitoring.database.location_repository import get_field_location_id, get_field_location_row_by_centroid

logger = logging.getLogger(__name__)

_NORMALIZE_VILLAGE_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def extract_village_token_from_placemark(name: str) -> Optional[str]:
    """First token of placemark name, lowercased, punctuation stripped."""
    if not name or not str(name).strip():
        return None
    text = str(name).strip().replace("_", " ")
    tokens = text.split()
    if not tokens:
        return None
    first = tokens[0].strip()
    for prefix in ("village-", "village_", "village:"):
        if first.lower().startswith(prefix):
            first = first[len(prefix) :].strip()
            break
    normalized = _NORMALIZE_VILLAGE_PUNCT.sub("", first).strip().lower()
    return normalized if normalized else None


def resolve_or_create_field_location(
    db: Session,
    lat: float,
    lon: float,
    *,
    placemark_name: str = "",
    geocode_delay_sec: float = 0.35,
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """
    If a field_locations row exists for the centroid (7 dp match), return (location_id, row_dict).
    Otherwise reverse-geocode (Google), insert, return (new location_id, row_dict).

    Logs (exact strings requested):
      - "Location found → skipping Google API"
      - "Location not found → calling Google API"
    """
    import random
    import time

    row = get_field_location_row_by_centroid(db, lat, lon)
    if row and row.get("location_id"):
        logger.info("Location found → skipping Google API")
        return str(row["location_id"]), row

    logger.info("Location not found → calling Google API")
    from services.google_reverse_geocode import get_location_from_coordinates

    geo: dict = {}
    for attempt in range(3):
        geo = get_location_from_coordinates(lat, lon)
        if geo.get("village") or geo.get("district") or geo.get("state"):
            break
        if attempt < 2:
            wait = 1.0 * (2**attempt) + random.uniform(0.1, 0.4)
            time.sleep(wait)
    time.sleep(geocode_delay_sec + random.uniform(0.0, 0.12))

    village = (geo.get("village") or "").strip() or "Unknown field"
    extracted_village = extract_village_token_from_placemark(placemark_name)

    loc_id = get_field_location_id(
        db,
        village,
        geo.get("district"),
        geo.get("state"),
        geo.get("state_code"),
        geo.get("mandal"),
        geo.get("postcode"),
        lat,
        lon,
        extracted_village=extracted_village,
    )
    if not loc_id:
        logger.warning("Google geocode + field_locations insert did not return location_id")
        return None, None

    row2 = get_field_location_row_by_centroid(db, lat, lon)
    return str(loc_id), row2
