"""
Sentinel-2 fetch with cloud fallback ladder + SCL-masked Statistical evalscripts.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from crop_monitoring.satellite_pipeline.cloud_config import DEFAULT_S2_CLOUD, S2CloudSettings
from crop_monitoring.satellite_pipeline.cloud_mask import sufficient_valid_pixels
from crop_monitoring.satellite_pipeline.fetch_bulk import _statistical_request

logger = logging.getLogger(__name__)


def fetch_s2_bulk_masked(
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    *,
    cloud: Optional[S2CloudSettings] = None,
    resolution: int = 10,
    evalscript_version: int = 3,
) -> tuple[list[dict], list[dict], Optional[dict], Optional[dict], float]:
    """
    Parallel masked Statistical requests for one date chunk.
    Returns (index_rows, band_rows, raw_idx, raw_bands, maxcc_used).
    """
    from crop_monitoring.satellite_pipeline.evalscripts import (
        STATISTICAL_S2_BANDS_MASKED_V3,
        STATISTICAL_S2_BANDS_V2,
        STATISTICAL_S2_INDICES_MASKED_V3,
        STATISTICAL_S2_INDICES_V2,
    )
    from crop_monitoring.satellite_pipeline.layers.harmonize import (
        parse_s2_band_stats_response,
        parse_s2_index_stats_response,
    )

    cfg = cloud or DEFAULT_S2_CLOUD
    if evalscript_version >= 3:
        es_i, es_b = STATISTICAL_S2_INDICES_MASKED_V3, STATISTICAL_S2_BANDS_MASKED_V3
    else:
        es_i, es_b = STATISTICAL_S2_INDICES_V2, STATISTICAL_S2_BANDS_V2

    kwargs = dict(
        geometry_geojson=geometry_geojson,
        start_date=start_date,
        end_date=end_date,
        resolution=resolution,
    )

    index_rows: list[dict] = []
    band_rows: list[dict] = []
    raw_idx = raw_bands = None
    maxcc_used = cfg.max_scene_cloud_pct

    for maxcc in cfg.effective_ladder():
        logger.info(
            "S2 Statistical chunk %s..%s maxcc=%s (SCL-masked v%s)",
            start_date,
            end_date,
            maxcc,
            evalscript_version,
        )
        kw = {**kwargs, "maxcc": maxcc}
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_i = ex.submit(
                _statistical_request,
                evalscript=es_i,
                parser=lambda item: parse_s2_index_stats_response(item, version=evalscript_version),
                **kw,
            )
            fut_b = ex.submit(
                _statistical_request,
                evalscript=es_b,
                parser=lambda item: parse_s2_band_stats_response(item, version=evalscript_version),
                **kw,
            )
            index_rows, raw_idx = fut_i.result()
            band_rows, raw_bands = fut_b.result()

        maxcc_used = maxcc
        if sufficient_valid_pixels(index_rows, min_valid_pct=cfg.min_valid_pixel_pct):
            logger.info(
                "S2 chunk satisfied at maxcc=%s (%d daily rows)",
                maxcc,
                len(index_rows),
            )
            break
        if not index_rows:
            logger.warning("S2 chunk empty at maxcc=%s; trying higher scene cloud limit", maxcc)
        else:
            best = max(
                (float(r.get("valid_pixel_percentage") or 0) for r in index_rows),
                default=0.0,
            )
            logger.warning(
                "S2 chunk max valid pixels=%.1f%% < %.1f%% at maxcc=%s; escalating",
                best,
                cfg.min_valid_pixel_pct,
                maxcc,
            )

    return index_rows, band_rows, raw_idx, raw_bands, maxcc_used
