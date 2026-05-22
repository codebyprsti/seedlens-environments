"""
Layer 1: Raw ingestion — store full API payloads before any transformation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date
from typing import Any, Optional

from sqlalchemy.orm import Session

from crop_monitoring.database.raw_observation_repository import (
    insert_satellite_raw_observation,
    json_sanitize_for_jsonb,
)
from crop_monitoring.satellite_pipeline.bands import PIPELINE_VERSION

logger = logging.getLogger(__name__)


def payload_checksum(payload: Any) -> str:
    raw = json.dumps(json_sanitize_for_jsonb(payload), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def store_raw(
    db: Session,
    *,
    location_id: str,
    file_name: str,
    satellite: str,
    source: str,
    api_type: str,
    observation_date: Optional[date | str] = None,
    season_id: Optional[str] = None,
    run_id: Optional[uuid.UUID] = None,
    product_id: Optional[str] = None,
    interval_start: Optional[str] = None,
    interval_end: Optional[str] = None,
    cloud_cover_pct: Optional[float] = None,
    valid_pixel_fraction: Optional[float] = None,
    max_cloud_cover_pct: float = 20.0,
    raw_response: Any = None,
    bands: Any = None,
    indices: Any = None,
    metadata: Optional[dict] = None,
    valid_pixel_percentage: Optional[float] = None,
    cloud_pixel_percentage: Optional[float] = None,
    usable_scene: Optional[bool] = None,
    quality_score: Optional[float] = None,
    internal_id: Optional[str] = None,
    grower_name: Optional[str] = None,
    grower_id: Optional[str] = None,
) -> Optional[int]:
    """
    Insert raw row and optionally patch extended columns when present in DB.
    """
    rid = insert_satellite_raw_observation(
        db,
        location_id=location_id,
        file_name=file_name,
        source=source,
        observation_date=observation_date,
        raw_response=raw_response,
        bands=bands,
        indices=indices,
    )
    if rid is None:
        return None

    meta = {
        "pipeline_version": PIPELINE_VERSION,
        "satellite": satellite,
        "api_type": api_type,
        **(metadata or {}),
    }
    if valid_pixel_percentage is not None:
        meta["valid_pixel_percentage"] = valid_pixel_percentage
    if cloud_pixel_percentage is not None:
        meta["cloud_pixel_percentage"] = cloud_pixel_percentage
    if usable_scene is not None:
        meta["usable_scene"] = usable_scene
    if quality_score is not None:
        meta["quality_score"] = quality_score
    chk = payload_checksum(raw_response or bands or indices or meta)

    try:
        from sqlalchemy import text

        db.execute(
            text("""
                UPDATE operations.satellite_raw_observation
                SET run_id = COALESCE(:run_id, run_id),
                    satellite = COALESCE(:satellite, satellite),
                    season_id = COALESCE(:season_id, season_id),
                    api_type = COALESCE(:api_type, api_type),
                    product_id = COALESCE(:product_id, product_id),
                    cloud_cover_pct = COALESCE(:cloud_cover_pct, cloud_cover_pct),
                    valid_pixel_fraction = COALESCE(:valid_pixel_fraction, valid_pixel_fraction),
                    max_cloud_cover_pct = COALESCE(:max_cloud_cover_pct, max_cloud_cover_pct),
                    payload_checksum = COALESCE(:payload_checksum, payload_checksum),
                    metadata = COALESCE(CAST(:metadata AS jsonb), metadata),
                    processing_status = 'stored',
                    valid_pixel_percentage = COALESCE(:valid_pixel_percentage, valid_pixel_percentage),
                    cloud_pixel_percentage = COALESCE(:cloud_pixel_percentage, cloud_pixel_percentage),
                    usable_scene = COALESCE(:usable_scene, usable_scene),
                    quality_score = COALESCE(:quality_score, quality_score),
                    internal_id = COALESCE(:internal_id, internal_id),
                    grower_name = COALESCE(:grower_name, grower_name),
                    grower_id = COALESCE(:grower_id, grower_id)
                WHERE id = :id
            """),
            {
                "id": rid,
                "run_id": str(run_id) if run_id else None,
                "satellite": satellite,
                "season_id": season_id,
                "api_type": api_type,
                "product_id": product_id,
                "cloud_cover_pct": cloud_cover_pct,
                "valid_pixel_fraction": valid_pixel_fraction,
                "max_cloud_cover_pct": max_cloud_cover_pct,
                "payload_checksum": chk,
                "metadata": json.dumps(json_sanitize_for_jsonb(meta)),
                "valid_pixel_percentage": valid_pixel_percentage,
                "cloud_pixel_percentage": cloud_pixel_percentage,
                "usable_scene": usable_scene,
                "quality_score": quality_score,
                "internal_id": internal_id,
                "grower_name": grower_name,
                "grower_id": grower_id,
            },
        )
    except Exception as e:
        logger.debug("Extended raw columns not updated (migration may be pending): %s", e)

    return rid
