"""
Sentinel Hub Statistical API client for 3-month daily aggregated S2 indices.

Uses aggregationInterval: P1D to retrieve one mean per index per day in a single request.
Reuses CDSE config from sentinel_client; does not rewrite index formulas (evalscript matches
index_calculator / pipeline conventions).
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any, Optional

from crop_monitoring.sh_http_setup import configure_sh_http

logger = logging.getLogger(__name__)

configure_sh_http()

try:
    from sentinelhub import (
        CRS,
        DataCollection,
        Geometry,
        SentinelHubStatistical,
        SHConfig,
    )
except ImportError:
    SentinelHubStatistical = None
    SHConfig = None

# Evalscript outputs 9 bands: NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, NDWI (LAI derived from EVI in post-processing).
# CDSE Statistical API requires dataMask in output when using L2A.
STATISTICAL_INDICES_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["B02", "B03", "B04", "B05", "B08", "B11", "dataMask"],
    output: [
      { id: "indices", bands: 9, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1, sampleType: "UINT8" }
    ]
  };
}
function evaluatePixel(sample) {
  var B02 = sample.B02, B03 = sample.B03, B04 = sample.B04, B05 = sample.B05;
  var B08 = sample.B08, B11 = sample.B11;
  var ndvi = 0, savi = 0, ndmi = 0, ndre = 0, gci = 0, psri = 0, msavi = 0, evi = 0, ndwi = 0;
  var d_nr = B08 + B04;
  if (d_nr > 0) {
    ndvi = (B08 - B04) / d_nr;
    savi = ((B08 - B04) / (d_nr + 0.5)) * 1.5;
    var twoNIR = 2 * B08 + 1;
    var disc = twoNIR * twoNIR - 8 * (B08 - B04);
    msavi = disc >= 0 ? (twoNIR - Math.sqrt(disc)) / 2 : 0;
    var denom = B08 + 6 * B04 - 7.5 * B02 + 1;
    evi = denom !== 0 ? 2.5 * (B08 - B04) / denom : 0;
  }
  var d_ns = B08 + B11;
  if (d_ns > 0) ndmi = (B08 - B11) / d_ns;
  var d_re = B08 + B05;
  if (d_re > 0) ndre = (B08 - B05) / d_re;
  if (B03 > 0) gci = (B08 / B03) - 1;
  if (B08 > 0) psri = (B04 - B03) / B08;
  var d_gw = B03 + B08 + 1e-8;
  if (d_gw > 0) ndwi = Math.max(-1, Math.min(1, (B03 - B08) / d_gw));
  return { indices: [ndvi, savi, ndmi, ndre, gci, psri, msavi, evi, ndwi], dataMask: [sample.dataMask] };
}
"""

# Order of bands in evalscript output
STAT_INDEX_NAMES = ["NDVI", "SAVI", "NDMI", "NDRE", "GCI", "PSRI", "MSAVI", "EVI", "NDWI"]


def _get_config() -> Any:
    if SHConfig is None:
        raise RuntimeError("sentinelhub package not installed")
    config = SHConfig()
    try:
        from core.config import settings
        config.sh_client_id = getattr(settings, "SH_CLIENT_ID", None) or os.environ.get("SH_CLIENT_ID", "")
        config.sh_client_secret = getattr(settings, "SH_CLIENT_SECRET", None) or os.environ.get("SH_CLIENT_SECRET", "")
    except ImportError:
        config.sh_client_id = os.environ.get("SH_CLIENT_ID", "")
        config.sh_client_secret = os.environ.get("SH_CLIENT_SECRET", "")
    from crop_monitoring.sentinel_client import CDSE_BASE_URL, CDSE_TOKEN_URL
    config.sh_base_url = CDSE_BASE_URL
    config.sh_token_url = CDSE_TOKEN_URL
    if os.environ.get("SH_INSTANCE_ID"):
        config.instance_id = os.environ["SH_INSTANCE_ID"]
    return config


def fetch_s2_indices_timeseries(
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    *,
    config: Optional[Any] = None,
    maxcc: float = 60,
    resolution: int = 10,
) -> list[dict[str, Any]]:
    """
    One Statistical API request with aggregationInterval P1D for the given polygon and time range.

    Returns a list of dicts, one per observation interval:
      {
        "interval_from": "YYYY-MM-DD",
        "interval_to": "YYYY-MM-DD",
        "analysis_date": "YYYY-MM-DD",
        "NDVI": mean or None,
        "SAVI": mean or None,
        ...
        "EVI": mean or None,
        "LAI": mean or None,  # 3.618*EVI - 0.118
      }
    LST and SAR are not provided by this call (use Process API per interval if needed).
    """
    if SentinelHubStatistical is None:
        raise RuntimeError("sentinelhub package not installed (Statistical API)")
    config = config or _get_config()
    geom = Geometry(geometry_geojson, crs=CRS.WGS84)
    time_interval = (start_date, end_date)
    # Statistical API expects maxcc in [0, 1] (e.g. 0.2 for 20%)
    maxcc_01 = float(maxcc) if 0 <= maxcc <= 1 else float(maxcc) / 100.0

    try:
        dc = DataCollection.SENTINEL2_L2A.define_from("s2l2a", service_url=config.sh_base_url)
    except Exception:
        dc = getattr(DataCollection, "SENTINEL2_L2A", DataCollection.SENTINEL2_L2A)

    aggregation = SentinelHubStatistical.aggregation(
        evalscript=STATISTICAL_INDICES_EVALSCRIPT,
        time_interval=time_interval,
        aggregation_interval="P1D",
        resolution=(resolution, resolution),
    )
    input_data = SentinelHubStatistical.input_data(dc, maxcc=maxcc_01)

    request = SentinelHubStatistical(
        aggregation=aggregation,
        input_data=[input_data],
        geometry=geom,
        config=config,
    )
    try:
        stats_list = request.get_data()
    except ValueError as e:
        if "sh_client_id" in str(e).lower() or "sh_client_secret" in str(e).lower():
            raise RuntimeError(
                "Sentinel Hub credentials required. Set SH_CLIENT_ID and SH_CLIENT_SECRET (Copernicus Data Space)."
            ) from e
        raise
    rows = _parse_statistical_indices_stats_list(stats_list)
    logger.info(
        "[Statistical API] polygon time range %s–%s: %d daily intervals",
        start_date, end_date, len(rows),
    )
    return rows


def _parse_statistical_indices_stats_list(stats_list: list[Any] | None) -> list[dict[str, Any]]:
    """Parse Sentinel Hub Statistical response list into daily index rows."""
    if not stats_list:
        return []
    payload = stats_list[0]
    data = payload.get("data") or []
    rows: list[dict[str, Any]] = []
    for item in data:
        interval = item.get("interval") or {}
        from_ts = interval.get("from", "")
        to_ts = interval.get("to", "")
        analysis_date = to_ts[:10] if len(to_ts) >= 10 else (from_ts[:10] if len(from_ts) >= 10 else None)
        interval_from = from_ts[:10] if len(from_ts) >= 10 else None
        interval_to = to_ts[:10] if len(to_ts) >= 10 else None
        out = item.get("outputs") or {}
        bands_out = (out.get("indices") or {}).get("bands") or {}
        means: dict[str, Optional[float]] = {name: None for name in STAT_INDEX_NAMES}
        for i, name in enumerate(STAT_INDEX_NAMES):
            band_data = bands_out.get(f"B{i}") or bands_out.get(str(i))
            if band_data and isinstance(band_data.get("stats"), dict):
                mean_val = band_data["stats"].get("mean")
                if mean_val is not None:
                    means[name] = float(mean_val)
        evi_val = means.get("EVI")
        if evi_val is not None:
            means["LAI"] = 3.618 * evi_val - 0.118
        else:
            means["LAI"] = None
        row = {
            "interval_from": interval_from,
            "interval_to": interval_to,
            "analysis_date": analysis_date,
            **means,
        }
        rows.append(row)
    return rows


def fetch_s2_indices_timeseries_with_raw(
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    *,
    config: Optional[Any] = None,
    maxcc: float = 60,
    resolution: int = 10,
) -> tuple[list[dict[str, Any]], Any]:
    """
    Same as fetch_s2_indices_timeseries but also returns the first Statistical API payload dict
    (for JSONB raw storage). Tuple is (parsed_rows, raw_payload_or_None).
    """
    if SentinelHubStatistical is None:
        raise RuntimeError("sentinelhub package not installed (Statistical API)")
    config = config or _get_config()
    geom = Geometry(geometry_geojson, crs=CRS.WGS84)
    time_interval = (start_date, end_date)
    maxcc_01 = float(maxcc) if 0 <= maxcc <= 1 else float(maxcc) / 100.0

    try:
        dc = DataCollection.SENTINEL2_L2A.define_from("s2l2a", service_url=config.sh_base_url)
    except Exception:
        dc = getattr(DataCollection, "SENTINEL2_L2A", DataCollection.SENTINEL2_L2A)

    aggregation = SentinelHubStatistical.aggregation(
        evalscript=STATISTICAL_INDICES_EVALSCRIPT,
        time_interval=time_interval,
        aggregation_interval="P1D",
        resolution=(resolution, resolution),
    )
    input_data = SentinelHubStatistical.input_data(dc, maxcc=maxcc_01)

    request = SentinelHubStatistical(
        aggregation=aggregation,
        input_data=[input_data],
        geometry=geom,
        config=config,
    )
    try:
        stats_list = request.get_data()
    except ValueError as e:
        if "sh_client_id" in str(e).lower() or "sh_client_secret" in str(e).lower():
            raise RuntimeError(
                "Sentinel Hub credentials required. Set SH_CLIENT_ID and SH_CLIENT_SECRET (Copernicus Data Space)."
            ) from e
        raise
    raw_payload = stats_list[0] if stats_list else None
    rows = _parse_statistical_indices_stats_list(stats_list)
    logger.info(
        "[Statistical API] polygon time range %s–%s: %d daily intervals (with raw payload)",
        start_date, end_date, len(rows),
    )
    return rows, raw_payload
