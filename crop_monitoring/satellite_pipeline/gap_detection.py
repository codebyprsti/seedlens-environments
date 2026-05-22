"""Detect missing acquisition dates vs STAC catalogue and DB rows."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def expected_s2_revisit_dates(start: date, end: date, revisit_days: int = 5) -> list[str]:
    """Approximate clear revisit schedule (Sentinel-2 ~5 day at equator)."""
    out: list[str] = []
    cur = start
    while cur <= end:
        out.append(cur.isoformat())
        cur += timedelta(days=revisit_days)
    return out


def find_missing_dates(
    *,
    catalogue_dates: set[str],
    db_dates: set[str],
    statistical_dates: set[str],
) -> dict[str, Any]:
    """
    Compare STAC scene dates vs ingested sentinel2 dates vs Statistical output dates.
    """
    cat = set(catalogue_dates)
    db = set(db_dates)
    stat = set(statistical_dates)
    return {
        "in_catalog_not_in_db": sorted(cat - db),
        "in_stat_not_in_db": sorted(stat - db),
        "in_db_not_in_catalog": sorted(db - cat),
        "catalogue_count": len(cat),
        "db_count": len(db),
        "statistical_count": len(stat),
    }
