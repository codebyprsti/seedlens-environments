"""
Build time series of crop indices from operations.crop_indices.

Supports document concept: "date/time window corresponding to crop stage (e.g. 30 DAS, 60 DAS)"
by querying stored results by location, grower, variety, and date range.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import text


def get_time_series(
    session: Session,
    *,
    location_id: Optional[str] = None,
    grower_id: Optional[str] = None,
    variety_id: Optional[str] = None,
    village: Optional[str] = None,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    Query operations.crop_indices and return rows ordered by date for time-series charts.

    Filters are optional; at least one of location_id, grower_id, variety_id, village
    or date range is typically used.
    """
    q = """
        SELECT
            id, location_id, grower_id, variety_id,
            polygon_area, date_start, date_end,
            ndvi, savi, ndmi, ndre, gci, psri, msavi, evi, lai, lst_celsius,
            vv_db, vh_db, vh_vv_ratio,
            village, grower_name, variety_name, created_at
        FROM operations.crop_indices
        WHERE 1=1
    """
    params: dict[str, Any] = {}
    if location_id is not None:
        q += " AND location_id = :location_id"
        params["location_id"] = location_id
    if grower_id is not None:
        q += " AND grower_id = :grower_id"
        params["grower_id"] = grower_id
    if variety_id is not None:
        q += " AND variety_id = :variety_id"
        params["variety_id"] = variety_id
    if village is not None:
        q += " AND village ILIKE :village"
        params["village"] = f"%{village}%"
    if date_start is not None:
        q += " AND date_end >= :date_start"
        params["date_start"] = date_start
    if date_end is not None:
        q += " AND date_start <= :date_end"
        params["date_end"] = date_end
    q += " ORDER BY date_start, id"
    if limit is not None and limit > 0:
        q += " LIMIT :limit"
        params["limit"] = limit

    result = session.execute(text(q), params)
    rows = result.fetchall()
    return [dict(r._mapping) for r in rows]
