"""
SQLAlchemy queries: crop_indices (file_name, sentinel time series), optional PostGIS AOI.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


def crop_indices_files_for_locations(
    session: Session,
    location_ids: list[str],
    *,
    season_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Distinct (location_id, file_name) from operations.crop_indices for given field IDs.
    """
    if not location_ids:
        return []
    q = """
        SELECT DISTINCT location_id, file_name
        FROM operations.crop_indices
        WHERE location_id = ANY(:locs)
          AND file_name IS NOT NULL
          AND TRIM(file_name) <> ''
    """
    params: dict[str, Any] = {"locs": location_ids}
    if season_id:
        q += " AND season_id = :season"
        params["season"] = season_id
    q += " ORDER BY location_id, file_name"
    rows = session.execute(text(q), params).fetchall()
    return [dict(r._mapping) for r in rows]


def crop_indices_sentinel_series(
    session: Session,
    location_id: str,
    date_start: date,
    date_end: date,
    *,
    season_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Per-date Sentinel-derived rows from crop_indices (ndvi, dates, file_name)."""
    from crop_monitoring.database.repository import (
        _crop_indices_existing_columns,
        _crop_indices_sar_column_names,
    )
    from crop_monitoring.insert_validator import crop_indices_observation_date_column

    date_col = crop_indices_observation_date_column(session)
    if date_col not in ("index_date", "analysis_date"):
        raise RuntimeError(f"unexpected observation date column: {date_col!r}")

    existing = _crop_indices_existing_columns(session)
    vv_c, vh_c, rat_c = _crop_indices_sar_column_names(session)

    want: list[str] = [
        date_col,
        "date_start",
        "date_end",
        "file_name",
        "ndvi",
        "savi",
        "ndmi",
        "evi",
        "lai",
    ]
    if "lst_celsius" in existing:
        want.append("lst_celsius")
    elif "lst_c" in existing:
        want.append("lst_c")
    for c in (vv_c, vh_c, rat_c):
        if c in existing and c not in want:
            want.append(c)

    select_parts: list[str] = []
    for c in want:
        if c not in existing:
            continue
        if c == date_col:
            select_parts.append(f"{c} AS obs_date")
        else:
            select_parts.append(c)
    if not select_parts:
        raise RuntimeError("crop_indices: no selectable columns matched")

    q = f"""
        SELECT {", ".join(select_parts)}
        FROM operations.crop_indices
        WHERE location_id = :loc
          AND {date_col} IS NOT NULL
          AND {date_col} >= :ds
          AND {date_col} <= :de
    """
    params: dict[str, Any] = {"loc": location_id, "ds": date_start, "de": date_end}
    if season_id and "season_id" in existing:
        q += " AND season_id = :season"
        params["season"] = season_id
    q += f" ORDER BY {date_col}, id"
    rows = session.execute(text(q), params).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r._mapping)
        od = d.pop("obs_date", None)
        d["analysis_date"] = od
        if "lst_c" in d and "lst_celsius" not in d:
            d["lst_celsius"] = d.get("lst_c")
        if vv_c != "vv_db" and "vv_db" not in d:
            d["vv_db"] = d.get(vv_c)
        if vh_c != "vh_db" and "vh_db" not in d:
            d["vh_db"] = d.get(vh_c)
        if rat_c != "vh_vv_ratio" and "vh_vv_ratio" not in d:
            d["vh_vv_ratio"] = d.get(rat_c)
        out.append(d)
    return out


def field_location_centroid(
    session: Session,
    location_id: str,
) -> Optional[tuple[float, float]]:
    """Return (latitude, longitude) from operations.field_locations if present."""
    q = text(
        """
        SELECT latitude, longitude
        FROM operations.field_locations
        WHERE location_id = :loc
        ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
        LIMIT 1
        """
    )
    row = session.execute(q, {"loc": location_id}).fetchone()
    if not row:
        return None
    return (float(row[0]), float(row[1]))


def location_polygon_geojson_for_village_location(
    session: Session,
    location_id: str,
) -> Optional[dict[str, Any]]:
    """
    Village polygon from operations.location_polygons (L_* style IDs).
    Field IDs (IND-*) usually do not exist here — returns None in that case.
    """
    q = text(
        """
        SELECT polygon_geojson
        FROM operations.location_polygons
        WHERE location_id = :loc
        ORDER BY polygon_index
        LIMIT 1
        """
    )
    row = session.execute(q, {"loc": location_id}).fetchone()
    if not row or row[0] is None:
        return None
    gj = row[0]
    if isinstance(gj, dict):
        return gj
    return None
