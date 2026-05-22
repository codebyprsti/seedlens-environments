"""
Resolve grower_id, variety_id from master tables and insert crop_indices.

location_id for crop monitoring is NOT resolved here — use operations.field_locations + centroid
(see crop_monitoring.database.location_repository.get_field_location_row_by_centroid).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Optional, Tuple

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy import text

from crop_monitoring.database.id_resolution import (
    normalize_text,
    normalize_variety_name,
    fuzzy_match_grower,
    fuzzy_match_variety,
)
from crop_monitoring.database.master_repository import (
    create_grower,
    create_variety,
    get_crop_id_and_name_for_variety,
)

logger = logging.getLogger(__name__)

# Fuzzy match thresholds: Growers ≥85, Varieties ≥90
GROWER_MATCH_THRESHOLD = 85.0
VARIETY_MATCH_THRESHOLD = 90.0


def _grower_like_match(db: Session, grower_name: str, location_id: Optional[str]) -> Optional[tuple]:
    """LIKE match; returns (grower_id, grower_name) or None."""
    if not grower_name or not grower_name.strip():
        return None
    norm = normalize_text(grower_name)
    pattern = f"%{norm}%"
    if location_id is not None:
        try:
            row = db.execute(
                text("""
                    SELECT g.grower_id, g.grower_name FROM operations.growers g
                    WHERE LOWER(TRIM(g.grower_name)) LIKE :pattern
                    AND EXISTS (SELECT 1 FROM operations.season_crop_inspection_base i
                                WHERE i.grower_id = g.grower_id AND i.location_id = :loc)
                    LIMIT 1
                """),
                {"pattern": pattern, "loc": location_id},
            ).fetchone()
            if row:
                return (row[0], row[1])
        except Exception:
            pass
    row = db.execute(
        text("SELECT grower_id, grower_name FROM operations.growers WHERE LOWER(TRIM(grower_name)) LIKE :pattern LIMIT 1"),
        {"pattern": pattern},
    ).fetchone()
    return (row[0], row[1]) if row else None


def get_grower_id(db: Session, grower_name: str, location_id: Optional[str] = None) -> Optional[Tuple[str, str, float]]:
    """
    Resolve grower_id: normalize, try LIKE, then fuzzy (≥85).
    If no match: create new grower and return (id, grower_name, 100.0).
    Returns (id, matched_name, score) or None only when grower_name is empty.
    """
    if not grower_name or not grower_name.strip():
        logger.debug("[ID resolution] grower: extracted grower_name is empty")
        return None
    raw = grower_name.strip()
    row = _grower_like_match(db, grower_name, location_id)
    if row:
        gid, matched = row[0], row[1]
        logger.info(
            "Grower resolution:\n  Input: %s\n  Matched: %s\n  Method: LIKE\n  grower_id: %s",
            raw, matched, gid,
        )
        return (gid, matched, 100.0)
    result = fuzzy_match_grower(db, grower_name, location_id, threshold=GROWER_MATCH_THRESHOLD)
    if result:
        gid, matched_name, score = result
        logger.info(
            "Grower resolution:\n  Input: %s\n  Matched: %s\n  Score: %.1f\n  grower_id: %s",
            raw, matched_name, score, gid,
        )
        return (gid, matched_name, score)
    try:
        gid = create_grower(db, grower_name)
        return (gid, raw, 100.0)
    except Exception as e:
        logger.warning("[ID resolution] Could not create grower: %s", e)
        return None


def _variety_like_match(db: Session, variety_name: str) -> Optional[tuple]:
    """LIKE match (normalized, hyphen-agnostic); returns (variety_id, variety_name) or None."""
    if not variety_name or not variety_name.strip():
        return None
    norm = normalize_text(variety_name)
    if not norm:
        return None
    pattern = f"%{norm}%"
    row = db.execute(
        text("""
            SELECT variety_id, variety_name FROM operations.varieties
            WHERE REPLACE(LOWER(TRIM(variety_name)), '-', '') LIKE :pattern
            LIMIT 1
        """),
        {"pattern": pattern},
    ).fetchone()
    return (row[0], row[1]) if row else None


def get_variety_id(
    db: Session, variety_name: str
) -> Optional[Tuple[str, str, float, Optional[str], Optional[str]]]:
    """
    Resolve variety_id: normalize to USRH-<number>, try LIKE, then fuzzy (≥90).
    If no match: create new variety (with default crop) and return (id, name, 100.0, crop_id, crop_name).
    Returns (variety_id, matched_name, score, crop_id, crop_name) or None when variety_name is empty.
    """
    if not variety_name or not variety_name.strip():
        logger.debug("[ID resolution] variety: extracted variety_name is empty")
        return None
    raw = variety_name.strip()
    normalized = normalize_variety_name(raw)
    search_name = normalized if normalized else raw
    row = _variety_like_match(db, search_name)
    if row:
        vid, matched = row[0], row[1]
        crop_id, crop_name = get_crop_id_and_name_for_variety(db, vid)
        logger.info(
            "Variety resolution:\n  Input: %s\n  Matched: %s\n  Method: LIKE\n  variety_id: %s",
            raw, matched, vid,
        )
        return (vid, matched, 100.0, crop_id, crop_name)
    result = fuzzy_match_variety(db, search_name, threshold=VARIETY_MATCH_THRESHOLD)
    if result:
        vid, matched_name, score = result
        crop_id, crop_name = get_crop_id_and_name_for_variety(db, vid)
        logger.info(
            "Variety resolution:\n  Input: %s\n  Matched: %s\n  Score: %.1f\n  variety_id: %s",
            raw, matched_name, score, vid,
        )
        return (vid, matched_name, score, crop_id, crop_name)
    try:
        vid = create_variety(db, search_name, crop_id=None)
        crop_id, crop_name = get_crop_id_and_name_for_variety(db, vid)
        logger.info(
            "Variety resolution:\n  Input: %s\n  Created: %s\n  variety_id: %s",
            raw, search_name, vid,
        )
        return (vid, search_name, 100.0, crop_id, crop_name)
    except Exception as e:
        logger.warning("[ID resolution] Could not create variety: %s", e)
        return None


def _normalize_kml_file_name(file_name: Optional[str]) -> str:
    if not file_name:
        return ""
    return str(file_name).replace("+", " ").strip()


def crop_indices_file_processed_for_season(
    db: Session, file_name: str, season_id: str
) -> bool:
    """
    Check if any row exists in operations.crop_indices for (file_name, season_id).
    Used for resumable batch: skip file if already processed for this season.
    Returns False if season_id column missing or on error.
    """
    file_name = _normalize_kml_file_name(file_name)
    if not file_name or not season_id:
        return False
    try:
        row = db.execute(
            text("""
                SELECT 1 FROM operations.crop_indices
                WHERE file_name = :file_name AND season_id = :season_id
                LIMIT 1
            """),
            {"file_name": file_name, "season_id": season_id},
        ).fetchone()
        return row is not None
    except Exception as e:
        if "season_id" in str(e) or "column" in str(e).lower():
            logger.debug("crop_indices_file_processed_for_season skipped (season_id may be missing): %s", e)
        if isinstance(e, DBAPIError):
            try:
                db.rollback()
            except Exception:
                pass
        return False


def crop_indices_row_exists(
    db: Session, file_name: str, analysis_date: str, season_id: Optional[str] = None
) -> bool:
    """
    Check if a row already exists in operations.crop_indices for (file_name, observation date).
    Uses index_date or analysis_date per table schema. When season_id is set, only that season counts.
    """
    from crop_monitoring.insert_validator import crop_indices_observation_date_column

    file_name = _normalize_kml_file_name(file_name)
    if not file_name or not analysis_date:
        return False
    try:
        date_col = crop_indices_observation_date_column(db)
    except RuntimeError:
        # crop_indices_observation_date_column raises when neither column exists in metadata.
        date_col = "analysis_date"
    params: dict[str, Any] = {"file_name": file_name, "obs_date": analysis_date}
    try:
        if season_id:
            params["season_id"] = season_id
            if date_col == "index_date":
                row = db.execute(
                    text(
                        """
                SELECT 1 FROM operations.crop_indices
                WHERE file_name = :file_name AND index_date IS NOT DISTINCT FROM CAST(:obs_date AS date)
                  AND season_id = :season_id
                LIMIT 1
                """
                    ),
                    params,
                ).fetchone()
            else:
                row = db.execute(
                    text(
                        """
                SELECT 1 FROM operations.crop_indices
                WHERE file_name = :file_name AND analysis_date IS NOT DISTINCT FROM CAST(:obs_date AS date)
                  AND season_id = :season_id
                LIMIT 1
                """
                    ),
                    params,
                ).fetchone()
        else:
            if date_col == "index_date":
                row = db.execute(
                    text(
                        """
                SELECT 1 FROM operations.crop_indices
                WHERE file_name = :file_name AND index_date IS NOT DISTINCT FROM CAST(:obs_date AS date)
                LIMIT 1
                """
                    ),
                    params,
                ).fetchone()
            else:
                row = db.execute(
                    text(
                        """
                SELECT 1 FROM operations.crop_indices
                WHERE file_name = :file_name AND analysis_date IS NOT DISTINCT FROM CAST(:obs_date AS date)
                LIMIT 1
                """
                    ),
                    params,
                ).fetchone()
        return row is not None
    except Exception as e:
        if "file_name" in str(e) or "column" in str(e).lower():
            logger.debug("crop_indices_row_exists skipped (schema mismatch?): %s", e)
        if isinstance(e, DBAPIError):
            try:
                db.rollback()
            except Exception:
                pass
        return False


def crop_indices_observation_exists(
    db: Session,
    location_id: Optional[str],
    date_start: str,
    variety_id: Optional[str],
    file_name: str,
    season_id: Optional[str] = None,
) -> bool:
    """
    Check if a row exists for (location_id, observation day, variety_id, file_name).
    Uses index_date or analysis_date (whichever exists on operations.crop_indices), not date_start,
    because many deployments have no date_start column.
    """
    from crop_monitoring.insert_validator import crop_indices_observation_date_column

    file_name = _normalize_kml_file_name(file_name)
    if not file_name or not date_start:
        return False
    try:
        try:
            obs_col = crop_indices_observation_date_column(db)
        except Exception:
            obs_col = "index_date"
        if obs_col not in ("index_date", "analysis_date"):
            obs_col = "index_date"
        params: dict[str, Any] = {
            "file_name": file_name,
            "obs_day": date_start,
            "location_id": location_id,
            "variety_id": variety_id,
        }
        if season_id:
            params["season_id"] = season_id
            row = db.execute(
                text(
                    f"""
                SELECT 1 FROM operations.crop_indices
                WHERE file_name = :file_name AND {obs_col} IS NOT DISTINCT FROM CAST(:obs_day AS date)
                  AND (location_id = :location_id OR (:location_id IS NULL AND location_id IS NULL))
                  AND (variety_id = :variety_id OR (:variety_id IS NULL AND variety_id IS NULL))
                  AND season_id = :season_id
                LIMIT 1
                """
                ),
                params,
            ).fetchone()
        else:
            row = db.execute(
                text(
                    f"""
                SELECT 1 FROM operations.crop_indices
                WHERE file_name = :file_name AND {obs_col} IS NOT DISTINCT FROM CAST(:obs_day AS date)
                  AND (location_id = :location_id OR (:location_id IS NULL AND location_id IS NULL))
                  AND (variety_id = :variety_id OR (:variety_id IS NULL AND variety_id IS NULL))
                LIMIT 1
                """
                ),
                params,
            ).fetchone()
        return row is not None
    except Exception as e:
        logger.debug("crop_indices_observation_exists skipped: %s", e)
        if isinstance(e, DBAPIError):
            try:
                db.rollback()
            except Exception:
                pass
        return False


_CROP_INDICES_COLUMN_CACHE: dict[int, frozenset[str]] = {}


def _crop_indices_existing_columns(db: Session) -> frozenset[str]:
    """Column names present on operations.crop_indices (for schema-tolerant inserts)."""
    bind = db.get_bind()
    key = id(bind)
    if key in _CROP_INDICES_COLUMN_CACHE:
        return _CROP_INDICES_COLUMN_CACHE[key]
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'operations'
              AND table_name = 'crop_indices'
            """
        )
    ).fetchall()
    s = frozenset(str(r[0]) for r in rows if r and r[0])
    _CROP_INDICES_COLUMN_CACHE[key] = s
    return s


def _crop_indices_sar_column_names(db: Session) -> tuple[str, str, str]:
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'operations'
              AND table_name = 'crop_indices'
              AND column_name IN ('vv_db', 'vh_db', 'vv', 'vh', 'vh_vv_ratio', 'vh_vv')
            """
        )
    ).fetchall()
    cols = {str(r[0]) for r in rows if r and r[0]}
    vv_c = "vv_db" if "vv_db" in cols else "vv"
    vh_c = "vh_db" if "vh_db" in cols else "vh"
    rat_c = "vh_vv_ratio" if "vh_vv_ratio" in cols else "vh_vv"
    return vv_c, vh_c, rat_c


def _insert_crop_indices_row(db: Session, data: dict[str, Any]) -> int:
    """Execute INSERT only (no validation)."""
    logger.info(
        "Storing crop analysis with location_id=%r, grower_name=%r, variety_name=%r",
        data.get("location_id"),
        data.get("grower_name"),
        data.get("variety_name"),
    )

    work = dict(data)
    try:
        from crop_monitoring.insert_validator import crop_indices_observation_date_column

        _dcol = crop_indices_observation_date_column(db)
    except Exception:
        _dcol = "analysis_date"
    if _dcol == "index_date" and work.get("index_date") is None and work.get("analysis_date") is not None:
        work["index_date"] = work["analysis_date"]
    if _dcol == "analysis_date" and work.get("analysis_date") is None and work.get("index_date") is not None:
        work["analysis_date"] = work["index_date"]

    try:
        vv_c, vh_c, rat_c = _crop_indices_sar_column_names(db)
    except Exception:
        vv_c, vh_c, rat_c = "vv_db", "vh_db", "vh_vv_ratio"
    vv_val = work.get("vv_db") if work.get("vv_db") is not None else work.get("vv")
    vh_val = work.get("vh_db") if work.get("vh_db") is not None else work.get("vh")
    rat_val = work.get("vh_vv_ratio") if work.get("vh_vv_ratio") is not None else work.get("vh_vv")
    work[vv_c] = vv_val
    work[vh_c] = vh_val
    work[rat_c] = rat_val

    columns = [
        "location_id",
        "grower_id",
        "variety_id",
        "polygon_id",
        "polygon_area",
        "date_start",
        "date_end",
        "area_acre",
        "distance_km",
        "extracted_grower",
        "ndvi",
        "savi",
        "ndmi",
        "ndre",
        "gci",
        "psri",
        "msavi",
        "evi",
        "lai",
        "lst_celsius",
        "ndwi_gao",
        "blue",
        "green",
        "red",
        "rededge1",
        "rededge2",
        "rededge3",
        "nir",
        "narrow_nir",
        "swir1",
        "swir2",
        vv_c,
        vh_c,
        rat_c,
        "grower_name",
        "variety_name",
    ]

    if _dcol == "index_date":
        optional_columns = ("file_name", "index_date", "season_id", "crop_id", "crop_name", "ndwi")
    else:
        optional_columns = ("file_name", "analysis_date", "season_id", "crop_id", "crop_name", "ndwi")
    for col in optional_columns:
        if col in work:
            columns.append(col)

    existing = _crop_indices_existing_columns(db)
    columns = [c for c in columns if c in existing]

    placeholders = [f":{c}" for c in columns]
    params = {c: work.get(c) for c in columns}
    query = text(
        f"""
            INSERT INTO operations.crop_indices (
                {", ".join(columns)}
            ) VALUES (
                {", ".join(placeholders)}
            )
            RETURNING id
        """
    )
    r = db.execute(query, params)
    row = r.fetchone()
    return int(row[0]) if row else 0


def _index_date_for_field_indices(data: dict[str, Any]) -> Optional[date]:
    v = data.get("index_date") or data.get("analysis_date") or data.get("date_start")
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if len(s) >= 10:
        s = s[:10]
    return date.fromisoformat(s)


def _insert_field_indices_row(db: Session, data: dict[str, Any]) -> int:
    """
    Mirror one crop_indices-equivalent row into operations.field_indices (lab / staging).
    Skips quietly if FIELD_INDICES_MIRROR=0 or table missing.
    """
    if os.environ.get("FIELD_INDICES_MIRROR", "1").strip().lower() in ("0", "false", "no"):
        return 0
    idx_dt = _index_date_for_field_indices(data)
    params = {
        "location_id": data.get("location_id"),
        "season_id": data.get("season_id"),
        "file_name": data.get("file_name"),
        "index_date": idx_dt,
        "grower_id": data.get("grower_id"),
        "variety_id": data.get("variety_id"),
        "crop_id": data.get("crop_id"),
        "crop_name": data.get("crop_name"),
        "polygon_id": data.get("polygon_id"),
        "polygon_area": data.get("polygon_area"),
        "date_start": data.get("date_start"),
        "date_end": data.get("date_end"),
        "area_acre": data.get("area_acre"),
        "distance_km": data.get("distance_km"),
        "extracted_grower": data.get("extracted_grower"),
        "centroid_lat": data.get("centroid_lat") if data.get("centroid_lat") is not None else data.get("latitude"),
        "centroid_lon": data.get("centroid_lon") if data.get("centroid_lon") is not None else data.get("longitude"),
        "ndvi": data.get("ndvi"),
        "savi": data.get("savi"),
        "ndmi": data.get("ndmi"),
        "ndre": data.get("ndre"),
        "gci": data.get("gci"),
        "psri": data.get("psri"),
        "msavi": data.get("msavi"),
        "evi": data.get("evi"),
        "lai": data.get("lai"),
        "ndwi": data.get("ndwi"),
        "ndwi_gao": data.get("ndwi_gao"),
        "blue": data.get("blue"),
        "green": data.get("green"),
        "red": data.get("red"),
        "rededge1": data.get("rededge1"),
        "rededge2": data.get("rededge2"),
        "rededge3": data.get("rededge3"),
        "nir": data.get("nir"),
        "narrow_nir": data.get("narrow_nir"),
        "swir1": data.get("swir1"),
        "swir2": data.get("swir2"),
        "lst_celsius": data.get("lst_celsius"),
        "vv_db": data.get("vv_db") if data.get("vv_db") is not None else data.get("vv"),
        "vh_db": data.get("vh_db") if data.get("vh_db") is not None else data.get("vh"),
        "vh_vv_ratio": data.get("vh_vv_ratio"),
        "grower_name": data.get("grower_name"),
        "variety_name": data.get("variety_name"),
        "batch_tag": data.get("field_indices_batch_tag") or "from_pipeline",
    }
    q = text(
        """
        INSERT INTO operations.field_indices (
            location_id, season_id, file_name, index_date,
            grower_id, variety_id, crop_id, crop_name,
            polygon_id, polygon_area, date_start, date_end,
            area_acre, distance_km, extracted_grower,
            centroid_lat, centroid_lon,
            ndvi, savi, ndmi, ndre, gci, psri, msavi, evi, lai, ndwi, ndwi_gao,
            blue, green, red, rededge1, rededge2, rededge3, nir, narrow_nir, swir1, swir2,
            lst_celsius, vv_db, vh_db, vh_vv_ratio,
            grower_name, variety_name, batch_tag
        ) VALUES (
            :location_id, :season_id, :file_name, :index_date,
            :grower_id, :variety_id, :crop_id, :crop_name,
            :polygon_id, :polygon_area, :date_start, :date_end,
            :area_acre, :distance_km, :extracted_grower,
            :centroid_lat, :centroid_lon,
            :ndvi, :savi, :ndmi, :ndre, :gci, :psri, :msavi, :evi, :lai, :ndwi, :ndwi_gao,
            :blue, :green, :red, :rededge1, :rededge2, :rededge3, :nir, :narrow_nir, :swir1, :swir2,
            :lst_celsius, :vv_db, :vh_db, :vh_vv_ratio,
            :grower_name, :variety_name, :batch_tag
        )
        RETURNING id
        """
    )
    try:
        r = db.execute(q, params)
        row = r.fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        err = str(e).lower()
        if "field_indices" in err and ("does not exist" in err or "relation" in err):
            logger.warning(
                "operations.field_indices insert skipped (table missing). "
                "Create with sql/create_field_indices_table.sql: %s",
                e,
            )
            return 0
        raise


def insert_crop_indices(
    db: Session,
    data: dict[str, Any],
    *,
    counters: Optional[dict[str, int]] = None,
    skip_duplicate_file_check: bool = False,
) -> int:
    """
    Pre-insert validation (centroid resolution, duplicates) then insert one row.
    Returns inserted id, or 0 if skipped by validation.
    """
    from crop_monitoring.insert_validator import validate_and_insert_crop_indices

    row_id = validate_and_insert_crop_indices(
        db,
        data,
        insert_fn=_insert_crop_indices_row,
        counters=counters,
        skip_duplicate_file_check=skip_duplicate_file_check,
    )
    if row_id > 0:
        _insert_field_indices_row(db, data)
    return row_id
