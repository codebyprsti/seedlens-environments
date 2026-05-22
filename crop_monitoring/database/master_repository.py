"""
Create new master records when fuzzy matching fails.
ID generation: continue sequence from MAX(id) in operations.locations (for create_location), growers, varieties.
Used by repository.get_grower_id / get_variety_id when fuzzy match fails.
(create_location remains for non–crop-monitoring callers; crop_indices location_id uses field_locations.)
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

# category_id used by existing record_service for operations masters
CATEGORY_LOCATION = 100004
CATEGORY_GROWER = 100005
CATEGORY_VARIETY = 100003
CATEGORY_CROP = 100001

# Default crop name when creating a new variety (e.g. USRH-24 -> Sunflower)
DEFAULT_CROP_NAME = "Sunflower"


def _next_numeric_id(db: Session, table_schema: str, table_name: str, id_column: str) -> int:
    """Get MAX(id_column), extract numeric part, return max_num + 1. Uses operations schema."""
    q = text(f"SELECT MAX({id_column}) FROM {table_schema}.{table_name}")
    row = db.execute(q).scalar()
    if not row:
        return 1
    match = re.search(r"\d+", str(row))
    return int(match.group()) + 1 if match else 1


def get_next_location_id(db: Session) -> str:
    """Next L_<number> from operations.locations."""
    num = _next_numeric_id(db, "operations", "locations", "location_id")
    return f"L_{num}"


def get_next_grower_id(db: Session) -> str:
    """Next G_<number> from operations.growers (e.g. G_300203)."""
    num = _next_numeric_id(db, "operations", "growers", "grower_id")
    return f"G_{num}"


def get_next_variety_id(db: Session) -> str:
    """Next VR_<number> from operations.varieties (e.g. VR_1003)."""
    num = _next_numeric_id(db, "operations", "varieties", "variety_id")
    return f"VR_{num}"


def create_location(
    db: Session,
    village: str,
    district: Optional[str] = None,
    state: Optional[str] = None,
) -> str:
    """
    Insert a new row into operations.locations; return new location_id.
    Uses village as location_name and unique_location_id. Safe for empty district/state.
    """
    if not village or not str(village).strip():
        raise ValueError("village is required for create_location")
    village_clean = str(village).strip()[:100]
    district_clean = (district and str(district).strip())[:100] if district else None
    state_clean = (state and str(state).strip())[:100] if state else None

    location_id = get_next_location_id(db)
    try:
        db.execute(
            text("""
                INSERT INTO operations.locations
                (location_id, village, unique_location_id, mandal, district, state, category_id)
                VALUES (:location_id, :village, :unique_location_id, NULL, :district, :state, :category_id)
            """),
            {
                "location_id": location_id,
                "village": village_clean,
                "unique_location_id": village_clean,
                "district": district_clean,
                "state": state_clean,
                "category_id": CATEGORY_LOCATION,
            },
        )
        db.commit()
        logger.info("New location created: %s (village=%s, district=%s, state=%s)", location_id, village_clean, district_clean, state_clean)
        return location_id
    except Exception as e:
        db.rollback()
        logger.exception("create_location failed: %s", e)
        raise


def create_grower(db: Session, grower_name: str) -> str:
    """Insert a new row into operations.growers; return new grower_id."""
    if not grower_name or not str(grower_name).strip():
        raise ValueError("grower_name is required for create_grower")
    name_clean = str(grower_name).strip()[:200]

    grower_id = get_next_grower_id(db)
    try:
        db.execute(
            text("""
                INSERT INTO operations.growers (grower_id, grower_name, fathers_name, grower_gender, category_id)
                VALUES (:grower_id, :grower_name, NULL, NULL, :category_id)
            """),
            {"grower_id": grower_id, "grower_name": name_clean, "category_id": CATEGORY_GROWER},
        )
        db.commit()
        logger.info("New grower created: %s (grower_name=%s)", grower_id, name_clean)
        return grower_id
    except Exception as e:
        db.rollback()
        logger.exception("create_grower failed: %s", e)
        raise


def resolve_or_create_crop_id(db: Session, crop_name: str = DEFAULT_CROP_NAME) -> str:
    """Return existing crop_id for crop_name, or create new crop and return crop_id."""
    name_clean = (crop_name or DEFAULT_CROP_NAME).strip()[:100]
    if not name_clean:
        name_clean = DEFAULT_CROP_NAME
    row = db.execute(
        text("SELECT crop_id FROM operations.crops WHERE LOWER(TRIM(crop_name)) = LOWER(:name) LIMIT 1"),
        {"name": name_clean},
    ).fetchone()
    if row:
        return row[0]
    num = _next_numeric_id(db, "operations", "crops", "crop_id")
    crop_id = f"CR_{str(num).zfill(3)}"
    try:
        db.execute(
            text("""
                INSERT INTO operations.crops (crop_id, crop_name, category_id)
                VALUES (:crop_id, :crop_name, :category_id)
            """),
            {"crop_id": crop_id, "crop_name": name_clean, "category_id": CATEGORY_CROP},
        )
        db.commit()
        logger.info("New crop created: %s (crop_name=%s)", crop_id, name_clean)
        return crop_id
    except Exception as e:
        db.rollback()
        logger.exception("resolve_or_create_crop_id failed: %s", e)
        raise


def create_variety(db: Session, variety_name: str, crop_id: Optional[str] = None) -> str:
    """
    Insert a new row into operations.varieties; return new variety_id.
    If crop_id is None, resolves or creates default crop (e.g. Sunflower).
    """
    if not variety_name or not str(variety_name).strip():
        raise ValueError("variety_name is required for create_variety")
    name_clean = str(variety_name).strip()[:200]
    if not crop_id:
        crop_id = resolve_or_create_crop_id(db, DEFAULT_CROP_NAME)

    variety_id = get_next_variety_id(db)
    try:
        db.execute(
            text("""
                INSERT INTO operations.varieties (variety_id, variety_name, crop_id, category_id)
                VALUES (:variety_id, :variety_name, :crop_id, :category_id)
            """),
            {
                "variety_id": variety_id,
                "variety_name": name_clean,
                "crop_id": crop_id,
                "category_id": CATEGORY_VARIETY,
            },
        )
        db.commit()
        logger.info("New variety created: %s (variety_name=%s, crop_id=%s)", variety_id, name_clean, crop_id)
        return variety_id
    except Exception as e:
        db.rollback()
        logger.exception("create_variety failed: %s", e)
        raise


def get_crop_id_and_name_for_variety(db: Session, variety_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (crop_id, crop_name) for the given variety_id from operations.varieties + operations.crops."""
    if not variety_id or not str(variety_id).strip():
        return (None, None)
    row = db.execute(
        text("""
            SELECT v.crop_id, c.crop_name
            FROM operations.varieties v
            LEFT JOIN operations.crops c ON c.crop_id = v.crop_id
            WHERE v.variety_id = :vid
            LIMIT 1
        """),
        {"vid": variety_id.strip()},
    ).fetchone()
    if not row:
        return (None, None)
    return (row[0], row[1])
