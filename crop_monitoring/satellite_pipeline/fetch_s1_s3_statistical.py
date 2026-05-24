"""
Bulk Statistical API fetchers for Sentinel-1 GRD and Sentinel-3 SLSTR (P1D daily means).

Replaces per-day Process API calls while preserving calendar-day row expansion downstream.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Optional

from crop_monitoring.satellite_pipeline.evalscripts import (
    STATISTICAL_S1_GRD_V1,
    STATISTICAL_S3_SLSTR_V1,
)
from crop_monitoring.satellite_pipeline.fetch_bulk import statistical_request_daily
from crop_monitoring.satellite_pipeline.layers.harmonize import (
    expand_s1s3_to_calendar,
    parse_s1_stats_response,
    parse_s3_stats_response,
    rows_by_acquisition_date,
)
from crop_monitoring.satellite_pipeline.temporal_batch import BatchStrategy, chunk_date_range

logger = logging.getLogger(__name__)

S1_STATISTICAL_RESOLUTION = int(os.environ.get("S1_STATISTICAL_RESOLUTION", "10"))
S3_STATISTICAL_RESOLUTION = int(os.environ.get("S3_STATISTICAL_RESOLUTION", "1000"))


def use_s1s3_statistical_api() -> bool:
    return (os.environ.get("SATELLITE_S1S3_STATISTICAL", "true") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def fetch_s1_bulk_statistical(
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    *,
    batch_strategy: BatchStrategy = "quarterly",
    resolution: int = S1_STATISTICAL_RESOLUTION,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Quarterly-chunked S1 Statistical API (P1D).
    Returns (daily_rows, raw_chunk_payloads).
    """
    start_d = date.fromisoformat(start_date[:10])
    end_d = date.fromisoformat(end_date[:10])
    all_rows: list[dict[str, Any]] = []
    raw_chunks: list[dict[str, Any]] = []

    for c0, c1 in chunk_date_range(start_d, end_d, batch_strategy):
        c0s, c1s = c0.isoformat(), c1.isoformat()
        logger.info("S1 Statistical chunk %s..%s", c0s, c1s)
        rows, meta = statistical_request_daily(
            geometry_geojson=geometry_geojson,
            start_date=c0s,
            end_date=c1s,
            evalscript=STATISTICAL_S1_GRD_V1,
            parser=parse_s1_stats_response,
            collection_type="sentinel-1-grd",
            resolution=resolution,
        )
        all_rows.extend(rows)
        raw_chunks.append(meta)

    return all_rows, raw_chunks


def fetch_s3_bulk_statistical(
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    *,
    batch_strategy: BatchStrategy = "quarterly",
    resolution: int = S3_STATISTICAL_RESOLUTION,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Quarterly-chunked S3 SLSTR Statistical API (P1D).
    Returns (daily_rows, raw_chunk_payloads).
    """
    start_d = date.fromisoformat(start_date[:10])
    end_d = date.fromisoformat(end_date[:10])
    all_rows: list[dict[str, Any]] = []
    raw_chunks: list[dict[str, Any]] = []

    for c0, c1 in chunk_date_range(start_d, end_d, batch_strategy):
        c0s, c1s = c0.isoformat(), c1.isoformat()
        logger.info("S3 Statistical chunk %s..%s", c0s, c1s)
        rows, meta = statistical_request_daily(
            geometry_geojson=geometry_geojson,
            start_date=c0s,
            end_date=c1s,
            evalscript=STATISTICAL_S3_SLSTR_V1,
            parser=parse_s3_stats_response,
            collection_type="sentinel-3-slstr",
            resolution=resolution,
        )
        all_rows.extend(rows)
        raw_chunks.append(meta)

    return all_rows, raw_chunks


def fetch_s1s3_bulk_calendar(
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    calendar_dates: list[str],
    *,
    batch_strategy: BatchStrategy = "quarterly",
) -> tuple[dict[str, tuple[Any, ...]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """
    Bulk S1 + S3 Statistical fetch, expanded to every calendar date.

    Returns:
        s1s3_by_date — (s7, s8, s9, lst_c, vv, vh) per calendar day
        s1_raw_chunks, s3_raw_chunks — full API payloads per temporal chunk
        api_stats — {s1_requests, s3_requests, s1_intervals, s3_intervals}
    """
    s1_rows, s1_raw = fetch_s1_bulk_statistical(
        geometry_geojson, start_date, end_date, batch_strategy=batch_strategy
    )
    s3_rows, s3_raw = fetch_s3_bulk_statistical(
        geometry_geojson, start_date, end_date, batch_strategy=batch_strategy
    )
    s1_by = rows_by_acquisition_date(s1_rows)
    s3_by = rows_by_acquisition_date(s3_rows)
    s1s3_by_date = expand_s1s3_to_calendar(calendar_dates, s1_by, s3_by)
    api_stats = {
        "s1_requests": len(s1_raw),
        "s3_requests": len(s3_raw),
        "s1_intervals": len(s1_rows),
        "s3_intervals": len(s3_rows),
        "calendar_days": len(calendar_dates),
        "process_api_calls_equivalent": len(calendar_dates) * 2,
    }
    logger.info(
        "S1/S3 Statistical bulk: %d S1 + %d S3 requests (was ~%d Process calls); "
        "%d calendar days expanded",
        api_stats["s1_requests"],
        api_stats["s3_requests"],
        api_stats["process_api_calls_equivalent"],
        api_stats["calendar_days"],
    )
    return s1s3_by_date, s1_raw, s3_raw, api_stats


def load_s1s3_from_stored_raw(
    raw_s1_payload: Any,
    raw_s3_payload: Any,
    calendar_dates: list[str],
) -> dict[str, tuple[Any, ...]]:
    """Rebuild s1s3_by_date from stored bulk Statistical raw JSON (reprocess path)."""
    from crop_monitoring.satellite_pipeline.layers.harmonize import daily_items_from_stored_raw

    s1_rows: list[dict] = []
    s3_rows: list[dict] = []

    def _collect(payload: Any, parser) -> list[dict]:
        rows: list[dict] = []
        if isinstance(payload, dict) and payload.get("chunks"):
            payload = payload["chunks"]
        if isinstance(payload, list):
            for chunk in payload:
                if isinstance(chunk, dict) and chunk.get("data"):
                    rows.extend(parser(d) for d in chunk["data"])
                elif isinstance(chunk, dict):
                    rows.extend(parser(d) for d in daily_items_from_stored_raw(chunk))
        elif isinstance(payload, dict):
            rows.extend(parser(d) for d in daily_items_from_stored_raw(payload))
        return rows

    s1_rows = _collect(raw_s1_payload, parse_s1_stats_response)
    s3_rows = _collect(raw_s3_payload, parse_s3_stats_response)
    return expand_s1s3_to_calendar(
        calendar_dates,
        rows_by_acquisition_date(s1_rows),
        rows_by_acquisition_date(s3_rows),
    )
