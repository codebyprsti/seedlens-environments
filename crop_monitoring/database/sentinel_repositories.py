"""
Repositories for operations.sentinel{1,2,3}_indices and ingestion checkpoints.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_V3_UNIQUE_INDEX = {
    "sentinel1_indices": "uq_s1_loc_season_acq_sat",
    "sentinel2_indices": "uq_s2_loc_season_acq_sat",
    "sentinel3_indices": "uq_s3_loc_season_acq_sat",
}
_SATELLITE_SOURCE = {
    "sentinel1_indices": "S1",
    "sentinel2_indices": "S2",
    "sentinel3_indices": "S3",
}


def _table_exists(db: Session, table: str) -> bool:
    row = db.execute(
        text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'operations' AND table_name = :t LIMIT 1
        """),
        {"t": table},
    ).fetchone()
    return row is not None


def _column_exists(db: Session, table: str, column: str) -> bool:
    row = db.execute(
        text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'operations' AND table_name = :t AND column_name = :c
            LIMIT 1
        """),
        {"t": table, "c": column},
    ).fetchone()
    return row is not None


def _uses_v3_unique(db: Session, table: str) -> bool:
    idx = _V3_UNIQUE_INDEX.get(table)
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


def _enrich_harvest_fields(data: dict[str, Any], *, satellite: Optional[str] = None) -> dict[str, Any]:
    ad = data.get("acquisition_date") or data.get("observation_date")
    if ad and not data.get("observation_date"):
        data["observation_date"] = ad
    if ad and not data.get("acquisition_date"):
        data["acquisition_date"] = ad
    if data.get("internal_id") is None and data.get("location_id"):
        data["internal_id"] = str(data["location_id"]).upper()
    if satellite:
        data.setdefault("satellite_source", satellite)
    if data.get("cloud_coverage") is None and data.get("scene_cloud_cover_pct") is not None:
        data["cloud_coverage"] = data["scene_cloud_cover_pct"]
    return data


def sentinel2_row_exists(
    db: Session,
    location_id: str,
    file_name: str,
    acquisition_date: str,
    season_id: Optional[str],
    *,
    internal_id: Optional[str] = None,
) -> bool:
    if not _table_exists(db, "sentinel2_indices"):
        return False
    if _uses_v3_unique(db, "sentinel2_indices"):
        row = db.execute(
            text("""
                SELECT 1 FROM operations.sentinel2_indices
                WHERE location_id = :loc
                  AND season_id IS NOT DISTINCT FROM :sid
                  AND acquisition_date = CAST(:ad AS date)
                  AND satellite_source = 'S2'
                LIMIT 1
            """),
            {
                "loc": location_id,
                "sid": season_id,
                "ad": acquisition_date,
            },
        ).fetchone()
        return row is not None
    if internal_id:
        row = db.execute(
            text("""
                SELECT 1 FROM operations.sentinel2_indices
                WHERE location_id = :loc
                  AND season_id IS NOT DISTINCT FROM :sid
                  AND internal_id = :iid
                  AND observation_date = CAST(:ad AS date)
                LIMIT 1
            """),
            {
                "loc": location_id,
                "sid": season_id,
                "iid": internal_id,
                "ad": acquisition_date,
            },
        ).fetchone()
        return row is not None
    row = db.execute(
        text("""
            SELECT 1 FROM operations.sentinel2_indices
            WHERE location_id = :loc AND file_name = :fn
              AND acquisition_date = CAST(:ad AS date)
              AND season_id IS NOT DISTINCT FROM :sid
            LIMIT 1
        """),
        {"loc": location_id, "fn": file_name, "ad": acquisition_date, "sid": season_id},
    ).fetchone()
    return row is not None


def _build_upsert_sql(
    table: str,
    cols: list[str],
    db: Session,
    *,
    update_cols: list[str],
):
    if _uses_v3_unique(db, table):
        conflict = "(location_id, season_id, acquisition_date, satellite_source)"
    else:
        conflict = "(location_id, file_name, season_id, acquisition_date)"

    value_exprs = []
    for c in cols:
        if c == "index_sources":
            value_exprs.append(f"CAST(:{c} AS jsonb)")
        elif c in ("bands_missing", "indices_missing"):
            value_exprs.append(f"CAST(:{c} AS text[])")
        else:
            value_exprs.append(f":{c}")

    set_clause = ",\n                ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    return text(f"""
            INSERT INTO operations.{table} ({", ".join(cols)})
            VALUES ({", ".join(value_exprs)})
            ON CONFLICT {conflict}
            DO UPDATE SET
                {set_clause},
                updated_at = NOW()
            RETURNING id
        """)


def _ensure_grower_on_data(db: Session, data: dict[str, Any], *, location_id: Optional[str] = None) -> None:
    if data.get("grower_id") or not data.get("grower_name"):
        return
    from crop_monitoring.satellite_pipeline.grower_resolver import ensure_grower_linked

    gid, gname, _ = ensure_grower_linked(
        db,
        grower_name=data.get("grower_name"),
        location_id=location_id or data.get("location_id"),
    )
    if gid:
        data["grower_id"] = gid
        data["grower_name"] = gname


def upsert_sentinel2_indices(db: Session, data: dict[str, Any]) -> Optional[int]:
    if not _table_exists(db, "sentinel2_indices"):
        logger.warning("operations.sentinel2_indices not found — run sql/create_sentinel_indices_tables.sql")
        return None
    data = _enrich_harvest_fields(dict(data), satellite="S2")
    _ensure_grower_on_data(db, data)
    cols = [
        "location_id", "file_name", "season_id", "acquisition_date", "product_id",
        "scene_cloud_cover_pct", "max_cloud_cover_pct", "raw_observation_id",
        "b01", "b02", "b03", "b04", "b05", "b06", "b07", "b08", "b8a", "b09", "b11", "b12",
        "ndvi", "savi", "msavi", "evi", "lai", "gci", "ndre", "ndre2", "cire", "mcari",
        "ndmi", "ndwi", "ndwi_gao", "mndwi", "psri", "gndvi", "msi",
        "valid_pixel_percentage", "cloud_pixel_percentage", "shadow_pixel_percentage",
        "masked_pixel_percentage", "usable_scene", "quality_score",
        "index_sources", "bands_missing", "indices_missing", "pipeline_version",
    ]
    if _column_exists(db, "sentinel2_indices", "observation_date"):
        cols.append("observation_date")
    if _column_exists(db, "sentinel2_indices", "internal_id"):
        cols.append("internal_id")
    if _column_exists(db, "sentinel2_indices", "grower_name"):
        cols.extend(["grower_name", "grower_id"])
    if _column_exists(db, "sentinel2_indices", "satellite_source"):
        cols.extend(["satellite_source", "cloud_coverage", "orbit_direction", "processing_level"])

    if data.get("indices_missing") is None:
        data["indices_missing"] = []
    if data.get("bands_missing") is None:
        data["bands_missing"] = []
    params = {c: data.get(c) for c in cols}
    if params.get("index_sources") is not None and not isinstance(params["index_sources"], str):
        params["index_sources"] = json.dumps(params["index_sources"])

    update_cols = [
        "ndvi", "valid_pixel_percentage", "usable_scene", "quality_score",
        "raw_observation_id", "index_sources", "bands_missing",
    ]
    if "grower_name" in cols:
        update_cols.extend(["grower_name", "grower_id", "internal_id"])
    if "cloud_coverage" in cols:
        update_cols.append("cloud_coverage")

    r = db.execute(_build_upsert_sql("sentinel2_indices", cols, db, update_cols=update_cols), params)
    row = r.fetchone()
    return int(row[0]) if row else None


def upsert_sentinel1_indices(db: Session, data: dict[str, Any]) -> Optional[int]:
    if not _table_exists(db, "sentinel1_indices"):
        return None
    data = _enrich_harvest_fields(dict(data), satellite="S1")
    _ensure_grower_on_data(db, data)
    cols = [
        "location_id", "file_name", "season_id", "acquisition_date", "product_id",
        "raw_observation_id", "vv", "vh", "vv_db", "vh_db", "vh_vv_ratio", "rvi", "cross_pol_ratio",
        "index_sources", "bands_missing", "pipeline_version",
    ]
    if _column_exists(db, "sentinel1_indices", "observation_date"):
        cols.append("observation_date")
    if _column_exists(db, "sentinel1_indices", "internal_id"):
        cols.append("internal_id")
    if _column_exists(db, "sentinel1_indices", "grower_name"):
        cols.extend(["grower_name", "grower_id"])
    if _column_exists(db, "sentinel1_indices", "satellite_source"):
        cols.extend(["satellite_source", "cloud_coverage", "orbit_direction", "processing_level"])
    if not data.get("processing_level"):
        data["processing_level"] = "GRD"

    params = {c: data.get(c) for c in cols}
    if params.get("index_sources") is not None and not isinstance(params["index_sources"], str):
        params["index_sources"] = json.dumps(params["index_sources"])

    update_cols = ["vv_db", "vh_db", "vh_vv_ratio", "vv", "vh"]
    if "grower_name" in cols:
        update_cols.extend(["grower_name", "grower_id", "internal_id"])

    r = db.execute(_build_upsert_sql("sentinel1_indices", cols, db, update_cols=update_cols), params)
    row = r.fetchone()
    return int(row[0]) if row else None


def upsert_sentinel3_indices(db: Session, data: dict[str, Any]) -> Optional[int]:
    if not _table_exists(db, "sentinel3_indices"):
        return None
    data = _enrich_harvest_fields(dict(data), satellite="S3")
    _ensure_grower_on_data(db, data)
    cols = [
        "location_id", "file_name", "season_id", "acquisition_date", "product_id",
        "raw_observation_id", "s7", "s8", "s9", "lst_k", "lst_celsius", "lst_delta_k",
        "index_sources", "bands_missing", "pipeline_version",
    ]
    if _column_exists(db, "sentinel3_indices", "observation_date"):
        cols.append("observation_date")
    if _column_exists(db, "sentinel3_indices", "internal_id"):
        cols.append("internal_id")
    if _column_exists(db, "sentinel3_indices", "grower_name"):
        cols.extend(["grower_name", "grower_id"])
    if _column_exists(db, "sentinel3_indices", "satellite_source"):
        cols.extend(["satellite_source", "cloud_coverage", "orbit_direction", "processing_level"])
    if not data.get("processing_level"):
        data["processing_level"] = "L1B"

    params = {c: data.get(c) for c in cols}
    if params.get("index_sources") is not None and not isinstance(params["index_sources"], str):
        params["index_sources"] = json.dumps(params["index_sources"])

    update_cols = ["lst_celsius", "lst_k", "s7", "s8", "s9"]
    if "grower_name" in cols:
        update_cols.extend(["grower_name", "grower_id", "internal_id"])

    r = db.execute(_build_upsert_sql("sentinel3_indices", cols, db, update_cols=update_cols), params)
    row = r.fetchone()
    return int(row[0]) if row else None


def create_ingestion_run(
    db: Session,
    *,
    run_id: str,
    mode: str,
    season_id: Optional[str],
    time_start: Optional[date],
    time_end: Optional[date],
) -> None:
    if not _table_exists(db, "satellite_ingestion_run"):
        return
    db.execute(
        text("""
            INSERT INTO operations.satellite_ingestion_run
                (run_id, mode, season_id, time_range_start, time_range_end, status)
            VALUES
                (CAST(:run_id AS uuid), :mode, :season_id, :t0, :t1, 'running')
            ON CONFLICT (run_id) DO NOTHING
        """),
        {"run_id": run_id, "mode": mode, "season_id": season_id, "t0": time_start, "t1": time_end},
    )


def finish_ingestion_run(
    db: Session,
    run_id: str,
    *,
    status: str,
    files_processed: int,
    files_failed: int,
) -> None:
    if not _table_exists(db, "satellite_ingestion_run"):
        return
    db.execute(
        text("""
            UPDATE operations.satellite_ingestion_run
            SET status = :status,
                files_processed = :ok,
                files_failed = :fail,
                finished_at = NOW()
            WHERE run_id = CAST(:run_id AS uuid)
        """),
        {"run_id": run_id, "status": status, "ok": files_processed, "fail": files_failed},
    )

