"""
Sentinel Hub Process API service: fetch raw Sentinel-2 L2A bands once, 10m resolution.

Single multi-band request (B02, B03, B04, B08, B8A, B11, B12, SCL). 20m bands are
resampled to 10m by the API. Used with index_calculator to derive layers in the backend.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Optional, Tuple

import numpy as np

try:
    from crop_monitoring.sh_http_setup import configure_sh_http

    configure_sh_http()
except ImportError:
    pass

logger = logging.getLogger(__name__)

try:
    from sentinelhub import (
        BBox,
        CRS,
        DataCollection,
        Geometry,
        MimeType,
        MosaickingOrder,
        SHConfig,
        SentinelHubRequest,
    )
    from sentinelhub.geo_utils import bbox_to_dimensions
    from sentinelhub.time_utils import parse_time_interval, serialize_time
except ImportError:
    SHConfig = None
    SentinelHubRequest = None

# CDSE config
CDSE_BASE_URL = "https://sh.dataspace.copernicus.eu"
CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_DEFAULT_SH_CLIENT_ID = os.environ.get("SH_CLIENT_ID", "")
_DEFAULT_SH_CLIENT_SECRET = os.environ.get("SH_CLIENT_SECRET", "")

# Single evalscript: output all raw bands at 10m (20m bands B8A,B11,B12 are resampled by the API)
EVALSCRIPT_RAW_BANDS = """
//VERSION=3
function setup() {
  return {
    input: ["B02", "B03", "B04", "B08", "B8A", "B11", "B12", "SCL"],
    output: { bands: 8, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [
    sample.B02,
    sample.B03,
    sample.B04,
    sample.B08,
    sample.B8A,
    sample.B11,
    sample.B12,
    sample.SCL
  ];
}
"""

# In-memory cache: key -> (bands_array, bbox, width, height, timestamp)
_raw_bands_cache: dict[str, Tuple[np.ndarray, tuple, int, int, float]] = {}
CACHE_TTL_SECONDS = 300
CACHE_MAX_ENTRIES = 20


def _get_config() -> Any:
    if SHConfig is None:
        raise RuntimeError("sentinelhub package not installed")
    config = SHConfig()
    config.sh_client_id = os.environ.get("SH_CLIENT_ID") or _DEFAULT_SH_CLIENT_ID
    config.sh_client_secret = os.environ.get("SH_CLIENT_SECRET") or _DEFAULT_SH_CLIENT_SECRET
    config.sh_base_url = CDSE_BASE_URL
    config.sh_token_url = CDSE_TOKEN_URL
    if os.environ.get("SH_INSTANCE_ID"):
        config.instance_id = os.environ["SH_INSTANCE_ID"]
    return config


def _cdse_input_data(time_interval: tuple, maxcc: float) -> list[dict]:
    start_time, end_time = serialize_time(
        parse_time_interval(time_interval, allow_undefined=True), use_tz=True
    )
    maxcc_pct = int(maxcc) if maxcc > 1 else int(maxcc * 100)
    return [
        {
            "type": DataCollection.SENTINEL2_L2A.api_id,
            "dataFilter": {
                "timeRange": {"from": start_time, "to": end_time},
                "maxCloudCoverage": maxcc_pct,
                "mosaickingOrder": MosaickingOrder.LEAST_CC.value,
            },
        }
    ]


def _cache_key(bbox: tuple, time_interval: tuple) -> str:
    b = tuple(round(x, 6) for x in bbox)
    return hashlib.sha256(f"{b!r}{time_interval!r}".encode()).hexdigest()


def _cache_get(key: str) -> Optional[Tuple[np.ndarray, tuple, int, int]]:
    if key not in _raw_bands_cache:
        return None
    arr, bbox, w, h, ts = _raw_bands_cache[key]
    if CACHE_TTL_SECONDS > 0 and (time.time() - ts) > CACHE_TTL_SECONDS:
        del _raw_bands_cache[key]
        return None
    return (arr, bbox, w, h)


def _cache_set(key: str, arr: np.ndarray, bbox: tuple, w: int, h: int) -> None:
    global _raw_bands_cache
    while len(_raw_bands_cache) >= CACHE_MAX_ENTRIES and _raw_bands_cache:
        oldest_key = min(_raw_bands_cache, key=lambda k: _raw_bands_cache[k][4])
        del _raw_bands_cache[oldest_key]
    _raw_bands_cache[key] = (arr, bbox, w, h, time.time())


def fetch_raw_bands(
    bbox: Tuple[float, float, float, float],
    time_interval: Tuple[str, str],
    *,
    config: Optional[Any] = None,
    maxcc: int = 20,
    resolution: int = 10,
    min_pixels: int = 64,
    use_cache: bool = True,
) -> Tuple[np.ndarray, Tuple[float, float, float, float], int, int]:
    """
    Fetch raw Sentinel-2 L2A bands (B02,B03,B04,B08,B8A,B11,B12,SCL) at 10m, CRS EPSG:4326.
    20m bands are resampled to 10m by the Process API.

    Returns:
        bands: (H, W, 8) float32 array, band order as in BAND_INDEX
        bbox: (min_lon, min_lat, max_lon, max_lat)
        width: pixel width
        height: pixel height
    """
    if SentinelHubRequest is None:
        raise RuntimeError("sentinelhub package not installed")
    config = config or _get_config()
    resolution = (resolution, resolution)
    if use_cache:
        key = _cache_key(bbox, time_interval)
        cached = _cache_get(key)
        if cached is not None:
            logger.debug("Sentinel raw bands cache hit for bbox=%s", bbox)
            return cached
    min_lon, min_lat, max_lon, max_lat = bbox
    sh_bbox = BBox(bbox, crs=CRS.WGS84)
    w, h = bbox_to_dimensions(sh_bbox, resolution=resolution[0])
    w = max(int(w), min_pixels)
    h = max(int(h), min_pixels)
    geom = Geometry({"type": "Polygon", "coordinates": [[
        [min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]
    ]]}, crs=CRS("EPSG:4326"))
    request = SentinelHubRequest(
        evalscript=EVALSCRIPT_RAW_BANDS,
        input_data=_cdse_input_data(time_interval, maxcc),
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        geometry=geom,
        size=(w, h),
        config=config,
    )
    data = request.get_data()
    if not data:
        raise RuntimeError("Sentinel Hub returned no data")
    arr = np.array(data[0], dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[:, :, np.newaxis]
    if arr.shape[2] != 8:
        raise RuntimeError(f"Expected 8 bands, got {arr.shape[2]}")
    if use_cache:
        _cache_set(key, arr, bbox, w, h)
    return (arr, bbox, w, h)


def clear_raw_bands_cache() -> None:
    """Clear the in-memory raw bands cache (e.g. for testing or memory pressure)."""
    global _raw_bands_cache
    _raw_bands_cache.clear()
