"""
Bulk CDSE fetchers — SCL-masked Statistical API with cloud fallback ladder.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from typing import Any, Callable

from crop_monitoring.satellite_pipeline.cloud_config import DEFAULT_S2_CLOUD, S2CloudSettings
from crop_monitoring.satellite_pipeline.temporal_batch import BatchStrategy, chunk_date_range

logger = logging.getLogger(__name__)

# Backward-compatible alias
MAX_CLOUD_COVER_PCT = DEFAULT_S2_CLOUD.max_scene_cloud_pct


def _statistical_request(
    *,
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    evalscript: str,
    parser: Callable[[dict], dict[str, Any]],
    maxcc: float,
    resolution: int = 10,
) -> tuple[list[dict[str, Any]], Optional[dict]]:
    """One S2 L2A Statistical API call (backward-compatible wrapper)."""
    rows, meta = statistical_request_daily(
        geometry_geojson=geometry_geojson,
        start_date=start_date,
        end_date=end_date,
        evalscript=evalscript,
        parser=parser,
        collection_type="sentinel-2-l2a",
        maxcc=maxcc,
        resolution=resolution,
    )
    meta["maxcc"] = maxcc
    return rows, meta


def statistical_request_daily(
    *,
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    evalscript: str,
    parser: Callable[[dict], dict[str, Any]],
    collection_type: str,
    maxcc: Optional[float] = None,
    resolution: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    One Statistical API call with P1D aggregation for any CDSE collection type.
    Returns (parsed_daily_rows, raw_meta).
    """
    from crop_monitoring.sh_http_setup import configure_sh_http
    from crop_monitoring.statistical_client import _get_config
    from crop_monitoring.satellite_pipeline.layers.harmonize import iter_statistical_daily_items

    configure_sh_http()

    try:
        from sentinelhub import CRS, DataCollection, Geometry, SentinelHubStatistical
    except ImportError as e:
        raise RuntimeError("sentinelhub package not installed (Statistical API)") from e

    config = _get_config()
    geom = Geometry(geometry_geojson, crs=CRS.WGS84)

    dc: Any = collection_type
    ctype = collection_type.lower()
    if ctype in ("sentinel-2-l2a", "s2l2a"):
        try:
            dc = DataCollection.SENTINEL2_L2A.define_from("s2l2a", service_url=config.sh_base_url)
        except Exception:
            dc = getattr(DataCollection, "SENTINEL2_L2A", DataCollection.SENTINEL2_L2A)
    elif ctype in ("sentinel-1-grd", "s1grd"):
        try:
            dc = DataCollection.SENTINEL1_GRD.define_from("s1grd", service_url=config.sh_base_url)
        except Exception:
            dc = getattr(DataCollection, "SENTINEL1_GRD", "sentinel-1-grd")
    elif ctype in ("sentinel-3-slstr", "s3slstr"):
        try:
            dc = DataCollection.SENTINEL3_SLSTR.define_from("s3slstr", service_url=config.sh_base_url)
        except Exception:
            dc = getattr(DataCollection, "SENTINEL3_SLSTR", "sentinel-3-slstr")

    aggregation = SentinelHubStatistical.aggregation(
        evalscript=evalscript,
        time_interval=(start_date, end_date),
        aggregation_interval="P1D",
        resolution=(resolution, resolution),
    )
    if maxcc is not None:
        maxcc_01 = float(maxcc) if 0 <= maxcc <= 1 else float(maxcc) / 100.0
        input_data = SentinelHubStatistical.input_data(dc, maxcc=maxcc_01)
    else:
        input_data = SentinelHubStatistical.input_data(dc)

    request = SentinelHubStatistical(
        aggregation=aggregation,
        input_data=[input_data],
        geometry=geom,
        config=config,
    )
    stats_list = request.get_data()
    daily = iter_statistical_daily_items(stats_list)
    rows = [parser(item) for item in daily]
    return rows, {
        "interval": [start_date, end_date],
        "collection_type": collection_type,
        "resolution": resolution,
        "data": daily,
        "response": stats_list,
    }


def fetch_s2_bulk_statistical(
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    *,
    maxcc: Optional[float] = None,
    cloud: Optional[S2CloudSettings] = None,
    resolution: int = 10,
    batch_strategy: BatchStrategy = "quarterly",
) -> tuple[list[dict], list[dict], Optional[dict], Optional[dict]]:
    """
    Temporal chunks + per-chunk cloud ladder (20→40→60→80) with SCL pixel masking.
    Returns (index_daily_rows, band_daily_rows, raw_indices_payload, raw_bands_payload).
    """
    cfg = cloud or DEFAULT_S2_CLOUD
    if maxcc is not None:
        cfg = S2CloudSettings.from_env({"max_scene_cloud_pct": float(maxcc)})

    start_d = date.fromisoformat(start_date[:10])
    end_d = date.fromisoformat(end_date[:10])

    all_idx: list[dict] = []
    all_bands: list[dict] = []
    raws_idx: list = []
    raws_bands: list = []

    from crop_monitoring.satellite_pipeline.fetch_s2_cloud import fetch_s2_bulk_masked

    for c0, c1 in chunk_date_range(start_d, end_d, batch_strategy):
        ir, br, raw_i, raw_b, _used = fetch_s2_bulk_masked(
            geometry_geojson,
            c0.isoformat(),
            c1.isoformat(),
            cloud=cfg,
            resolution=resolution,
        )
        all_idx.extend(ir)
        all_bands.extend(br)
        if raw_i:
            raws_idx.append(raw_i)
        if raw_b:
            raws_bands.append(raw_b)

    return all_idx, all_bands, raws_idx[0] if raws_idx else None, raws_bands[0] if raws_bands else None
