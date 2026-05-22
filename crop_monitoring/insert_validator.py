"""
Pre-insert validation for operations.crop_indices rows.

Parameterized SQL only (identifiers like observation date column are chosen via
information_schema, not user input).
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, MutableMapping, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SPECIAL_LOCATIONS = frozenset(
    {
        "IND-KA-600053",
        "IND-KA-600220",
        "IND-OD-604341",
        "IND-OD-605669",
    }
)

_OBS_DATE_COLUMN_CACHE: dict[int, str] = {}


def _engine_key(db: Session) -> int:
    return id(db.get_bind())


def crop_indices_observation_date_column(db: Session) -> str:
    """Return 'index_date' or 'analysis_date' — prefers index_date when both exist."""
    key = _engine_key(db)
    if key in _OBS_DATE_COLUMN_CACHE:
        return _OBS_DATE_COLUMN_CACHE[key]
    row = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'operations'
              AND table_name = 'crop_indices'
              AND column_name IN ('index_date', 'analysis_date')
            ORDER BY CASE column_name WHEN 'index_date' THEN 0 ELSE 1 END
            LIMIT 1
            """
        )
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError("operations.crop_indices has neither index_date nor analysis_date")
    col = str(row[0])
    _OBS_DATE_COLUMN_CACHE[key] = col
    return col


def _counter(counters: Optional[MutableMapping[str, int]], key: str, delta: int = 1) -> None:
    if counters is None:
        return
    counters[key] = int(counters.get(key, 0)) + delta


def _record_lat_lon(record: Mapping[str, Any]) -> tuple[Optional[float], Optional[float]]:
    lat = record.get("latitude")
    if lat is None:
        lat = record.get("centroid_lat")
    lon = record.get("longitude")
    if lon is None:
        lon = record.get("centroid_lon")
    try:
        if lat is not None:
            lat = float(lat)
        if lon is not None:
            lon = float(lon)
    except (TypeError, ValueError):
        return None, None
    return lat, lon


def normalize_file_name(record: MutableMapping[str, Any], counters: Optional[MutableMapping[str, int]]) -> None:
    """RULE 1: + → space, strip."""
    fn = record.get("file_name")
    if fn is None:
        return
    old = str(fn)
    new = old.replace("+", " ").strip()
    record["file_name"] = new
    if old != new:
        _counter(counters, "filenames_normalized", 1)


def resolve_location_id_from_centroid(db: Session, latitude: float, longitude: float) -> Optional[str]:
    """Match ``field_locations`` the same way as crop batch (7 dp + tolerance fallback)."""
    from crop_monitoring.database.location_repository import get_field_location_row_by_centroid

    row = get_field_location_row_by_centroid(db, latitude, longitude)
    if not row:
        return None
    lid = row.get("location_id")
    return str(lid) if lid else None


def location_id_exists(db: Session, location_id: str) -> bool:
    row = db.execute(
        text(
            """
            SELECT COUNT(*)::int
            FROM operations.field_locations
            WHERE location_id = :location_id
            """
        ),
        {"location_id": location_id},
    ).fetchone()
    return bool(row and row[0] and int(row[0]) > 0)


def duplicate_file_ingested(
    db: Session,
    *,
    season_id: str,
    location_id: str,
    file_name: str,
) -> bool:
    """RULE 5: same normalized file_name already stored for this location+season."""
    if not file_name or not season_id or not location_id:
        return False
    row = db.execute(
        text(
            """
            SELECT COUNT(*)::int
            FROM operations.crop_indices
            WHERE location_id = :location_id
              AND season_id = :season_id
              AND file_name = :file_name
            """
        ),
        {"location_id": location_id, "season_id": season_id, "file_name": file_name},
    ).fetchone()
    return bool(row and row[0] and int(row[0]) > 0)


def duplicate_observation_exists(
    db: Session,
    *,
    season_id: str,
    location_id: str,
    observation_date: Any,
    polygon_area: Any,
    date_col: str,
    file_name: Optional[str] = None,
) -> bool:
    """
    RULE 4: duplicate observation for the same KML / same calendar day.

    When ``file_name`` is set (normal path), the check is scoped to that file so rows from
    other KMLs at the same ``location_id`` do not suppress inserts. Production may also use
    ``uq_crop_indices_season_location_date_area`` without ``file_name``; see
    ``sql/alter_crop_indices_unique_season_loc_file_index.sql`` to align the DB with per-file days.
    """
    fn = (file_name or "").replace("+", " ").strip()
    file_clause = " AND file_name = :file_name " if fn else ""
    if date_col == "index_date":
        sql = f"""
            SELECT COUNT(*)::int
            FROM operations.crop_indices
            WHERE season_id = :season_id
              AND location_id = :location_id
              AND index_date IS NOT DISTINCT FROM :obs_date
              AND polygon_area IS NOT DISTINCT FROM :polygon_area
              {file_clause}
        """
    elif date_col == "analysis_date":
        sql = f"""
            SELECT COUNT(*)::int
            FROM operations.crop_indices
            WHERE season_id = :season_id
              AND location_id = :location_id
              AND analysis_date IS NOT DISTINCT FROM :obs_date
              AND polygon_area IS NOT DISTINCT FROM :polygon_area
              {file_clause}
        """
    else:
        raise ValueError("invalid observation date column")

    params: dict[str, Any] = {
        "season_id": season_id,
        "location_id": location_id,
        "obs_date": observation_date,
        "polygon_area": polygon_area,
    }
    if fn:
        params["file_name"] = fn
    row = db.execute(text(sql), params).fetchone()
    return bool(row and row[0] and int(row[0]) > 0)


def _observation_date_value(record: Mapping[str, Any], date_col: str) -> Any:
    if date_col == "index_date":
        v = record.get("index_date")
        if v is not None:
            return v
    v = record.get("analysis_date") or record.get("index_date")
    if v is not None:
        return v
    return record.get("date_start")


def _log_ctx(record: Mapping[str, Any], date_val: Any, *, area: Any, file_name: str) -> str:
    return (
        f"season={record.get('season_id')} location={record.get('location_id')} "
        f"date={date_val} area={area} file={file_name}"
    )


def validate_before_insert(
    db: Session,
    record: MutableMapping[str, Any],
    *,
    counters: Optional[MutableMapping[str, int]] = None,
    skip_duplicate_file_check: bool = False,
) -> bool:
    """
    Mutates record for RULE 1 and RULE 2. Returns True if insert should proceed, False if skipped.
    """
    normalize_file_name(record, counters)

    lat, lon = _record_lat_lon(record)
    if lat is None or lon is None:
        fn = record.get("file_name") or ""
        logger.warning(
            "[no_location] SKIPPED: No matching location_id found in field_locations for "
            "lat=%s, lng=%s, file=%s (missing latitude/longitude on record)",
            lat,
            lon,
            fn,
        )
        _counter(counters, "no_location", 1)
        return False

    old_loc = record.get("location_id")
    resolved = resolve_location_id_from_centroid(db, lat, lon)
    if resolved is None:
        logger.warning(
            "[no_location] SKIPPED: No matching location_id found in field_locations for "
            "lat=%s, lng=%s, file=%s",
            lat,
            lon,
            record.get("file_name") or "",
        )
        _counter(counters, "no_location", 1)
        return False

    if str(resolved) != str(old_loc):
        logger.info(
            "RESOLVED: location_id=%s from centroid lat=%s, lng=%s (was %s)",
            resolved,
            lat,
            lon,
            old_loc,
        )
    record["location_id"] = resolved

    if not location_id_exists(db, str(resolved)):
        logger.warning(
            "[invalid_location] SKIPPED: location_id=%s not found in field_locations "
            "after resolution, file=%s",
            resolved,
            record.get("file_name") or "",
        )
        _counter(counters, "invalid_location", 1)
        return False

    season_id = record.get("season_id")
    file_name = record.get("file_name") or ""
    if not skip_duplicate_file_check and file_name and season_id:
        if duplicate_file_ingested(
            db, season_id=str(season_id), location_id=str(resolved), file_name=str(file_name)
        ):
            logger.warning(
                "[duplicate_file] SKIPPED: File already ingested — file=%s, location=%s, season=%s",
                file_name,
                resolved,
                season_id,
            )
            _counter(counters, "duplicate_file", 1)
            return False

    date_col = crop_indices_observation_date_column(db)
    obs_date = _observation_date_value(record, date_col)
    polygon_area = record.get("polygon_area")

    if obs_date is None:
        logger.warning(
            "[no_observation_date] SKIPPED: missing index_date/analysis_date on record; %s",
            _log_ctx(record, obs_date, area=polygon_area, file_name=file_name),
        )
        _counter(counters, "invalid_record", 1)
        return False

    if duplicate_observation_exists(
        db,
        season_id=str(season_id),
        location_id=str(resolved),
        observation_date=obs_date,
        polygon_area=polygon_area,
        date_col=date_col,
        file_name=str(file_name) if file_name else None,
    ):
        loc_id = str(resolved)
        if loc_id in SPECIAL_LOCATIONS:
            logger.warning(
                "[duplicate_special] SKIPPED: Special location duplicate — location=%s, "
                "date=%s, area=%s, file=%s",
                loc_id,
                obs_date,
                polygon_area,
                file_name,
            )
            _counter(counters, "duplicate_special", 1)
        else:
            logger.warning(
                "[duplicate] SKIPPED: Duplicate record — season=%s, location=%s, date=%s, "
                "area=%s, file=%s",
                season_id,
                loc_id,
                obs_date,
                polygon_area,
                file_name,
            )
            _counter(counters, "duplicate", 1)
        return False

    return True


def new_counters() -> dict[str, int]:
    return {
        "inserted": 0,
        "duplicate": 0,
        "duplicate_special": 0,
        "duplicate_file": 0,
        "no_location": 0,
        "invalid_location": 0,
        "invalid_record": 0,
        "filenames_normalized": 0,
    }


def print_validation_batch_summary(file_name: str, counters: Mapping[str, int]) -> None:
    c = dict(counters)
    ins = int(c.get("inserted", 0))
    d_dup = int(c.get("duplicate", 0))
    d_sp = int(c.get("duplicate_special", 0))
    d_file = int(c.get("duplicate_file", 0))
    d_nl = int(c.get("no_location", 0))
    d_il = int(c.get("invalid_location", 0))
    d_ir = int(c.get("invalid_record", 0))
    fn_norm = int(c.get("filenames_normalized", 0))
    skipped = d_dup + d_sp + d_file + d_nl + d_il + d_ir
    processed = ins + skipped
    print("-------------------------------------")
    print(f"BATCH SUMMARY: {file_name}")
    print(f"Total processed   : {processed}")
    print(f"Total inserted    : {ins}")
    print(f"Total skipped     : {skipped}")
    print(f"  - Duplicate            : {d_dup}")
    print(f"  - Duplicate (special)  : {d_sp}")
    print(f"  - Duplicate file       : {d_file}")
    print(f"  - No location match    : {d_nl}")
    print(f"  - Invalid location     : {d_il}")
    print(f"  - Invalid record       : {d_ir}")
    print(f"Filenames normalized     : {fn_norm}")
    print("-------------------------------------")


def validate_and_insert_crop_indices(
    db: Session,
    record: MutableMapping[str, Any],
    *,
    insert_fn: Any,
    counters: Optional[MutableMapping[str, int]] = None,
    skip_duplicate_file_check: bool = False,
) -> int:
    """
    Run all pre-insert rules, then call insert_fn(db, record) which must perform the INSERT.

    insert_fn is typically crop_monitoring.database.repository._insert_crop_indices_row
    to avoid recursion.
    """
    if not validate_before_insert(
        db,
        record,
        counters=counters,
        skip_duplicate_file_check=skip_duplicate_file_check,
    ):
        return 0
    row_id = int(insert_fn(db, record))
    if row_id > 0:
        _counter(counters, "inserted", 1)
        if logger.isEnabledFor(logging.DEBUG):
            od = record.get("analysis_date") or record.get("index_date")
            logger.debug(
                "[inserted] %s",
                _log_ctx(record, od, area=record.get("polygon_area"), file_name=record.get("file_name") or ""),
            )
    return row_id
