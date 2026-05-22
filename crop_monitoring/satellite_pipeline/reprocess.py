"""
Reprocess harmonization + indices from stored raw rows (no Copernicus API calls).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from crop_monitoring.satellite_pipeline.layers.harmonize import (
    daily_items_from_stored_raw,
    merge_s2_daily_rows,
    parse_s2_band_stats_response,
    parse_s2_index_stats_response,
)
from crop_monitoring.satellite_pipeline.cloud_config import DEFAULT_S2_CLOUD
from crop_monitoring.satellite_pipeline.cloud_mask import assess_daily_quality
from crop_monitoring.satellite_pipeline.layers.indices import (
    build_sentinel1_record,
    build_sentinel2_record,
    build_sentinel3_record,
)
from crop_monitoring.database.sentinel_repositories import (
    upsert_sentinel1_indices,
    upsert_sentinel2_indices,
    upsert_sentinel3_indices,
)

logger = logging.getLogger(__name__)


def load_raw_rows(
    db: Session,
    *,
    location_id: str,
    file_name: str,
    source_prefix: Optional[str] = None,
) -> list[dict[str, Any]]:
    q = """
        SELECT id, source, raw_response, bands, indices, observation_date, metadata
        FROM operations.satellite_raw_observation
        WHERE location_id = :loc AND file_name = :fn
    """
    params: dict = {"loc": location_id, "fn": file_name}
    if source_prefix:
        q += " AND source LIKE :sp"
        params["sp"] = f"{source_prefix}%"
    q += " ORDER BY created_at ASC"
    rows = db.execute(text(q), params).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r[0],
            "source": r[1],
            "raw_response": r[2],
            "bands": r[3],
            "indices": r[4],
            "observation_date": r[5],
            "metadata": r[6],
        })
    return out


def load_s2_merged_days_from_raw(
    db: Session,
    *,
    location_id: str,
    file_name: str,
) -> list[dict]:
    """Parse stored raw into merged daily rows (no DB write)."""
    raw_rows = load_raw_rows(db, location_id=location_id, file_name=file_name)
    index_rows: list[dict] = []
    band_rows: list[dict] = []
    for row in raw_rows:
        src = row.get("source") or ""
        if "indices" in src:
            if isinstance(row.get("indices"), list) and row["indices"]:
                index_rows = row["indices"]
            elif row.get("raw_response"):
                data = daily_items_from_stored_raw(row["raw_response"])
                index_rows = [parse_s2_index_stats_response(d, version=3) for d in data]
        if "bands" in src:
            if isinstance(row.get("bands"), list) and row["bands"]:
                band_rows = row["bands"]
            elif row.get("raw_response"):
                data = daily_items_from_stored_raw(row["raw_response"])
                band_rows = [parse_s2_band_stats_response(d, version=3) for d in data]
    band_by_date = {r.get("acquisition_date"): r for r in band_rows if r.get("acquisition_date")}
    merged: list[dict] = []
    for ir in index_rows:
        ad = ir.get("acquisition_date")
        if ad:
            merged.append(merge_s2_daily_rows(band_by_date.get(ad, {"acquisition_date": ad}), ir))
    return merged


def reprocess_s2_from_raw(
    db: Session,
    *,
    location_id: str,
    file_name: str,
    season_id: Optional[str],
    max_cloud_cover: float = 20.0,
    internal_id: Optional[str] = None,
    grower_name: Optional[str] = None,
    grower_id: Optional[str] = None,
) -> int:
    """Rebuild sentinel2_indices from stored Statistical raw JSON / band rows."""
    raw_rows = load_raw_rows(db, location_id=location_id, file_name=file_name)
    index_rows: list[dict] = []
    band_rows: list[dict] = []
    raw_id: Optional[int] = None

    for row in raw_rows:
        src = row.get("source") or ""
        if "indices" in src and row.get("indices"):
            raw_id = raw_id or row["id"]
            if isinstance(row["indices"], list):
                index_rows = row["indices"]
            elif isinstance(row["raw_response"], dict):
                data = (row["raw_response"].get("response") or {}).get("data") or []
                index_rows = [parse_s2_index_stats_response(d) for d in data]
        if "bands" in src:
            if isinstance(row["bands"], list):
                band_rows = row["bands"]
            elif isinstance(row["raw_response"], dict):
                data = (row["raw_response"].get("response") or {}).get("data") or []
                band_rows = [parse_s2_band_stats_response(d) for d in data]

    if not index_rows and not band_rows:
        logger.warning("No S2 raw data to reprocess for %s", file_name)
        return 0

    band_by_date = {r.get("acquisition_date"): r for r in band_rows if r.get("acquisition_date")}
    n = 0
    for ir in index_rows:
        ad = ir.get("acquisition_date")
        if not ad:
            continue
        merged = merge_s2_daily_rows(band_by_date.get(ad, {"acquisition_date": ad}), ir)
        merged.update(
            assess_daily_quality(merged, min_valid_pct=DEFAULT_S2_CLOUD.min_valid_pixel_pct)
        )
        rec = build_sentinel2_record(
            merged,
            location_id=location_id,
            file_name=file_name,
            season_id=season_id,
            raw_observation_id=raw_id,
            max_cloud_cover_pct=max_cloud_cover,
            internal_id=internal_id,
            grower_name=grower_name,
            grower_id=grower_id,
        )
        if upsert_sentinel2_indices(db, rec):
            n += 1
    return n


def reprocess_file_from_raw(
    db: Session,
    *,
    location_id: str,
    file_name: str,
    season_id: Optional[str],
    max_cloud_cover: float = 20.0,
    internal_id: Optional[str] = None,
    grower_name: Optional[str] = None,
    grower_id: Optional[str] = None,
) -> dict[str, int]:
    """Reprocess all satellites where raw payloads exist."""
    counts = {"s2": 0, "s1": 0, "s3": 0}
    counts["s2"] = reprocess_s2_from_raw(
        db,
        location_id=location_id,
        file_name=file_name,
        season_id=season_id,
        max_cloud_cover=max_cloud_cover,
        internal_id=internal_id,
        grower_name=grower_name,
        grower_id=grower_id,
    )
    # S1/S3: reprocess from bands JSON in raw if present
    for row in load_raw_rows(db, location_id=location_id, file_name=file_name):
        src = row.get("source") or ""
        bands = row.get("bands") or {}
        if not isinstance(bands, dict):
            continue
        ad = row.get("observation_date")
        if ad and not hasattr(ad, "isoformat"):
            ad = str(ad)[:10]
        elif hasattr(ad, "isoformat"):
            ad = ad.isoformat()[:10]
        else:
            ad = None
        if "copernicus_s1" in src and ad:
            rec = build_sentinel1_record(
                bands.get("vv"),
                bands.get("vh"),
                location_id=location_id,
                file_name=file_name,
                season_id=season_id,
                acquisition_date=ad,
                raw_observation_id=row["id"],
                internal_id=internal_id,
                grower_name=grower_name,
                grower_id=grower_id,
            )
            if upsert_sentinel1_indices(db, rec):
                counts["s1"] += 1
        if "copernicus_lst" in src or "copernicus_s3" in src:
            lst = bands.get("lst_celsius")
            if lst is not None and ad:
                rec = build_sentinel3_record(
                    bands.get("s7"),
                    bands.get("s8"),
                    bands.get("s9"),
                    location_id=location_id,
                    file_name=file_name,
                    season_id=season_id,
                    acquisition_date=ad,
                    raw_observation_id=row["id"],
                    internal_id=internal_id,
                    grower_name=grower_name,
                    grower_id=grower_id,
                )
                if upsert_sentinel3_indices(db, rec):
                    counts["s3"] += 1
    return counts
