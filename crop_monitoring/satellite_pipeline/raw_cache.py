"""
Skip Copernicus API calls when raw payloads already stored for field/season/window.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def raw_exists_for_s2_season(
    db: Session,
    *,
    location_id: str,
    file_name: str,
    season_id: Optional[str],
    start_date: str,
    end_date: str,
) -> bool:
    """True if statistical S2 raw (indices layer) exists for this field and interval."""
    row = db.execute(
        text("""
            SELECT 1 FROM operations.satellite_raw_observation
            WHERE location_id = :loc
              AND file_name = :fn
              AND season_id IS NOT DISTINCT FROM :sid
              AND satellite = 'S2'
              AND source LIKE 'copernicus_s2%'
              AND raw_response IS NOT NULL
              AND (
                raw_response::text LIKE '%' || :start || '%'
                OR metadata::text LIKE '%' || :start || '%'
              )
            LIMIT 1
        """),
        {
            "loc": location_id,
            "fn": file_name,
            "sid": season_id,
            "start": start_date[:10],
        },
    ).fetchone()
    return row is not None


def _v3_unique_exists(db: Session, table: str) -> bool:
    idx = {
        "sentinel1_indices": "uq_s1_loc_season_acq_sat",
        "sentinel2_indices": "uq_s2_loc_season_acq_sat",
        "sentinel3_indices": "uq_s3_loc_season_acq_sat",
    }.get(table)
    if not idx:
        return False
    row = db.execute(
        text("""
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'operations' AND tablename = :t AND indexname = :idx
            LIMIT 1
        """),
        {"t": table, "idx": idx},
    ).fetchone()
    return row is not None


def indices_row_exists(
    db: Session,
    satellite: str,
    *,
    location_id: str,
    season_id: Optional[str],
    internal_id: str,
    observation_date: str,
) -> bool:
    table = {
        "S2": "sentinel2_indices",
        "S1": "sentinel1_indices",
        "S3": "sentinel3_indices",
    }.get(satellite.upper())
    if not table:
        return False
    sat_src = satellite.upper()
    if _v3_unique_exists(db, table):
        row = db.execute(
            text(f"""
                SELECT 1 FROM operations.{table}
                WHERE location_id = :loc
                  AND season_id IS NOT DISTINCT FROM :sid
                  AND acquisition_date = CAST(:od AS date)
                  AND satellite_source = :src
                LIMIT 1
            """),
            {"loc": location_id, "sid": season_id, "od": observation_date, "src": sat_src},
        ).fetchone()
        return row is not None
    row = db.execute(
        text(f"""
            SELECT 1 FROM operations.{table}
            WHERE location_id = :loc
              AND season_id IS NOT DISTINCT FROM :sid
              AND internal_id = :iid
              AND observation_date = CAST(:od AS date)
            LIMIT 1
        """),
        {
            "loc": location_id,
            "sid": season_id,
            "iid": internal_id,
            "od": observation_date,
        },
    ).fetchone()
    return row is not None


def raw_exists_for_s1s3_bulk(
    db: Session,
    *,
    location_id: str,
    file_name: str,
    season_id: Optional[str],
    start_date: str,
) -> bool:
    """True if bulk Statistical S1+S3 raw payloads exist for this field/season."""
    row = db.execute(
        text("""
            SELECT COUNT(DISTINCT satellite) AS n
            FROM operations.satellite_raw_observation
            WHERE location_id = :loc
              AND file_name = :fn
              AND season_id IS NOT DISTINCT FROM :sid
              AND satellite IN ('S1', 'S3')
              AND source IN ('copernicus_s1_statistical_v2', 'copernicus_s3_statistical_v2')
              AND raw_response IS NOT NULL
              AND (
                raw_response::text LIKE '%' || :start || '%'
                OR metadata::text LIKE '%' || :start || '%'
              )
        """),
        {
            "loc": location_id,
            "fn": file_name,
            "sid": season_id,
            "start": start_date[:10],
        },
    ).fetchone()
    return row is not None and int(row[0]) >= 2
