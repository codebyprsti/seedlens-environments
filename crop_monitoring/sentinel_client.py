"""
SentinelHub Process API client: Sentinel-2 L2A, Sentinel-3 SLSTR S8/S9, Sentinel-1 GRD VV/VH.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import numpy as np

from crop_monitoring.sh_http_setup import configure_sh_http

logger = logging.getLogger(__name__)

configure_sh_http()

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

CDSE_BASE_URL = "https://sh.dataspace.copernicus.eu"
CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"

# Sentinel-2 L2A full crop manifest (B10 cirrus often absent in L2A)
S2_BANDS_FULL = ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12"]
# Legacy 7-band + indices path (backward compatible)
S2_BANDS = ["B02", "B03", "B04", "B05", "B08", "B11", "B12"]
# Indices from evalscript after the 7 reflectance bands (channels 7,8,9)
S2_INDEX_BANDS_FROM_API = ["NDVI", "SAVI", "NDMI"]
EVALSCRIPT_S2 = """
//VERSION=3
function setup() {
  return {
    input: ["B02", "B03", "B04", "B05", "B08", "B11", "B12"],
    output: { bands: 10, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  var B02 = sample.B02, B03 = sample.B03, B04 = sample.B04, B05 = sample.B05;
  var B08 = sample.B08, B11 = sample.B11, B12 = sample.B12;
  var ndvi = 0, savi = 0, ndmi = 0;
  var d_nr = B08 + B04;
  if (d_nr > 0) {
    ndvi = (B08 - B04) / d_nr;
    savi = ((B08 - B04) / (d_nr + 0.5)) * 1.5;
  }
  var d_ns = B08 + B11;
  if (d_ns > 0) ndmi = (B08 - B11) / d_ns;
  return [B02, B03, B04, B05, B08, B11, B12, ndvi, savi, ndmi];
}
"""

# Sentinel-3 SLSTR L1B: S7, S8, S9 (brightness temperature Kelvin)
EVALSCRIPT_S3 = """
//VERSION=3
function setup() {
  return {
    input: ["S7", "S8", "S9"],
    output: { bands: 3, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [sample.S7, sample.S8, sample.S9];
}
"""

EVALSCRIPT_S2_FULL = """
//VERSION=3
function setup() {
  return {
    input: ["B01","B02","B03","B04","B05","B06","B07","B08","B8A","B09","B11","B12"],
    output: { bands: 12, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [
    sample.B01, sample.B02, sample.B03, sample.B04, sample.B05, sample.B06,
    sample.B07, sample.B08, sample.B8A, sample.B09, sample.B11, sample.B12
  ];
}
"""

# Sentinel-1 GRD: VV, VH (radar backscatter, linear power 0–~0.5; backend uses sentinel-1-grd + orthorectify)
EVALSCRIPT_S1 = """
//VERSION=3
function setup() {
  return {
    input: ["VV", "VH"],
    output: { bands: 2, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [sample.VV, sample.VH];
}
"""

_SWIR_EPS = 1e-9


def _warn_if_swir_invalid(bands_dict: dict[str, np.ndarray]) -> None:
    """Log once if B11/B12 missing or have no valid reflectance pixels."""
    for key in ("B11", "B12"):
        a = bands_dict.get(key)
        if a is None:
            logger.warning("SWIR bands not available")
            return
        v = np.asarray(a, dtype=np.float64).ravel()
        if not np.any(np.isfinite(v) & (v > _SWIR_EPS)):
            logger.warning("SWIR bands not available")
            return


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
    config.sh_base_url = CDSE_BASE_URL
    config.sh_token_url = CDSE_TOKEN_URL
    if os.environ.get("SH_INSTANCE_ID"):
        config.instance_id = os.environ["SH_INSTANCE_ID"]
    return config


def _cdse_s2_input(time_interval: tuple[str, str], maxcc: float = 60) -> list[dict]:
    start_time, end_time = serialize_time(
        parse_time_interval(time_interval, allow_undefined=True), use_tz=True
    )
    maxcc_pct = int(maxcc) if maxcc > 1 else int(maxcc * 100)
    return [{
        "type": DataCollection.SENTINEL2_L2A.api_id,
        "dataFilter": {
            "timeRange": {"from": start_time, "to": end_time},
            "maxCloudCoverage": maxcc_pct,
            "mosaickingOrder": MosaickingOrder.LEAST_CC.value,
        },
    }]


def _cdse_s3_input(time_interval: tuple[str, str]) -> list[dict]:
    start_time, end_time = serialize_time(
        parse_time_interval(time_interval, allow_undefined=True), use_tz=True
    )
    s3_id = getattr(DataCollection.SENTINEL3_SLSTR, "api_id", None) if hasattr(DataCollection, "SENTINEL3_SLSTR") else None
    return [{
        "type": s3_id or "sentinel-3-slstr",
        "dataFilter": {"timeRange": {"from": start_time, "to": end_time}},
    }]


def _cdse_s1_input(time_interval: tuple[str, str]) -> list[dict]:
    """
    Sentinel-1 GRD input for Process API (VV, VH backscatter).
    CDSE requires type "sentinel-1-grd", polarization DV for VV+VH, and orthorectify for correct geometry.
    """
    start_time, end_time = serialize_time(
        parse_time_interval(time_interval, allow_undefined=True), use_tz=True
    )
    s1_id = None
    if hasattr(DataCollection, "SENTINEL1_GRD"):
        s1_id = getattr(DataCollection.SENTINEL1_GRD, "api_id", None)
    if not s1_id and hasattr(DataCollection, "SENTINEL1_IW_GRD"):
        s1_id = getattr(DataCollection.SENTINEL1_IW_GRD, "api_id", None)
    if not s1_id and hasattr(DataCollection, "SENTINEL1_IW"):
        s1_id = getattr(DataCollection.SENTINEL1_IW, "api_id", None)
    # CDSE Process API expects "sentinel-1-grd" (see documentation.dataspace.copernicus.eu)
    collection_type = s1_id or "sentinel-1-grd"
    return [{
        "type": collection_type,
        "dataFilter": {
            "timeRange": {"from": start_time, "to": end_time},
            "resolution": "HIGH",
            "acquisitionMode": "IW",
            "polarization": "DV",
        },
        "processing": {"orthorectify": True},
    }]


def fetch_s2_bands(
    geometry_geojson: dict,
    time_interval: tuple[str, str],
    *,
    config: Optional[Any] = None,
    resolution: int = 10,
    min_pixels: int = 64,
    maxcc: float = 60,
) -> dict[str, np.ndarray]:
    """S2 L2A bands B02–B05, B08, B11, B12 plus NDVI/SAVI/NDMI (scene search ≤ maxcc; use v3 for SCL mask)."""
    if SentinelHubRequest is None:
        raise RuntimeError("sentinelhub package not installed")
    from shapely.geometry import shape
    geom = shape(geometry_geojson)
    bbox = geom.bounds
    sh_bbox = BBox(bbox, crs=CRS.WGS84)
    w, h = bbox_to_dimensions(sh_bbox, resolution=resolution)
    w, h = max(int(w), min_pixels), max(int(h), min_pixels)
    config = config or _get_config()
    geom_sh = Geometry(geometry_geojson, crs=CRS.WGS84)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S2,
        input_data=_cdse_s2_input(time_interval, maxcc),
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        geometry=geom_sh,
        size=(w, h),
        config=config,
    )
    try:
        data = req.get_data()
    except ValueError as e:
        if "sh_client_id" in str(e).lower() or "sh_client_secret" in str(e).lower():
            raise RuntimeError(
                "Sentinel Hub credentials required. Set SH_CLIENT_ID and SH_CLIENT_SECRET (Copernicus Data Space)."
            ) from e
        raise
    if not data:
        raise RuntimeError("Sentinel Hub returned no Sentinel-2 data")
    arr = np.array(data[0], dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[:, :, np.newaxis]
    bands_dict = {name: arr[:, :, i] for i, name in enumerate(S2_BANDS)}
    if arr.shape[2] >= 10:
        for j, name in enumerate(S2_INDEX_BANDS_FROM_API):
            bands_dict[name] = arr[:, :, 7 + j]
    elif arr.shape[2] >= 9:
        # Legacy 9-band response (no B12): keep B11, omit B12
        logger.warning("SWIR bands not available (B12 missing from response)")
        bands_dict = {name: arr[:, :, i] for i, name in enumerate(S2_BANDS[:-1])}
        for j, name in enumerate(S2_INDEX_BANDS_FROM_API):
            bands_dict[name] = arr[:, :, 6 + j]
        bands_dict["B12"] = np.full_like(bands_dict["B11"], np.nan)
    _warn_if_swir_invalid(bands_dict)
    # Diagnostic: raw band stats (before any masking)
    n_pixels = arr.shape[0] * arr.shape[1]
    for bname, band_arr in bands_dict.items():
        flat = np.asarray(band_arr).ravel()
        valid = np.isfinite(flat) & (flat > 0)
        n_valid = int(np.sum(valid))
        band_mean = float(np.mean(flat)) if flat.size else 0.0
        band_min = float(np.min(flat)) if flat.size else np.nan
        band_max = float(np.max(flat)) if flat.size else np.nan
        logger.info(
            "[S2 bands] %s shape=%s pixels=%d valid_gt0=%d mean=%.6f min=%.6f max=%.6f",
            bname, arr.shape[:2], n_pixels, n_valid, band_mean, band_min, band_max,
        )
    return bands_dict


def fetch_s2_bands_full(
    geometry_geojson: dict,
    time_interval: tuple[str, str],
    *,
    config: Optional[Any] = None,
    resolution: int = 10,
    min_pixels: int = 64,
    maxcc: float = 60,
) -> dict[str, np.ndarray]:
    """Process API: full S2 L2A band stack including B01 and B09 (scene search ≤ maxcc)."""
    if SentinelHubRequest is None:
        raise RuntimeError("sentinelhub package not installed")
    from shapely.geometry import shape

    geom = shape(geometry_geojson)
    sh_bbox = BBox(geom.bounds, crs=CRS.WGS84)
    w, h = bbox_to_dimensions(sh_bbox, resolution=resolution)
    w, h = max(int(w), min_pixels), max(int(h), min_pixels)
    config = config or _get_config()
    geom_sh = Geometry(geometry_geojson, crs=CRS.WGS84)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S2_FULL,
        input_data=_cdse_s2_input(time_interval, maxcc),
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        geometry=geom_sh,
        size=(w, h),
        config=config,
    )
    data = req.get_data()
    if not data:
        raise RuntimeError("Sentinel Hub returned no Sentinel-2 full-band data")
    arr = np.array(data[0], dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[:, :, np.newaxis]
    return {name: arr[:, :, i] for i, name in enumerate(S2_BANDS_FULL)}


def fetch_s3_thermal(
    geometry_geojson: dict,
    time_interval: tuple[str, str],
    *,
    config: Optional[Any] = None,
    resolution: int = 1000,
    min_pixels: int = 2,
    include_s7: bool = True,
) -> dict[str, np.ndarray]:
    """Process API: Sentinel-3 SLSTR S7, S8, S9 brightness temperature (Kelvin)."""
    if SentinelHubRequest is None:
        raise RuntimeError("sentinelhub package not installed")
    from shapely.geometry import shape
    geom = shape(geometry_geojson)
    bbox = geom.bounds
    sh_bbox = BBox(bbox, crs=CRS.WGS84)
    w, h = bbox_to_dimensions(sh_bbox, resolution=resolution)
    w, h = max(int(w), min_pixels), max(int(h), min_pixels)
    config = config or _get_config()
    geom_sh = Geometry(geometry_geojson, crs=CRS.WGS84)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S3,
        input_data=_cdse_s3_input(time_interval),
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        geometry=geom_sh,
        size=(w, h),
        config=config,
    )
    data = req.get_data()
    if not data:
        out = {"S8": np.full((h, w), np.nan), "S9": np.full((h, w), np.nan)}
        if include_s7:
            out["S7"] = np.full((h, w), np.nan)
        return out
    arr = np.array(data[0], dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[:, :, np.newaxis]
    out: dict[str, np.ndarray] = {}
    if include_s7 and arr.shape[2] >= 3:
        out["S7"] = arr[:, :, 0]
        out["S8"] = arr[:, :, 1]
        out["S9"] = arr[:, :, 2]
    elif arr.shape[2] >= 2:
        out["S8"] = arr[:, :, 0]
        out["S9"] = arr[:, :, 1]
    else:
        out["S8"] = arr[:, :, 0]
        out["S9"] = np.full_like(out["S8"], np.nan)
    return out


def fetch_s1_sar(
    geometry_geojson: dict,
    time_interval: tuple[str, str],
    *,
    config: Optional[Any] = None,
    resolution: int = 10,
    min_pixels: int = 64,
) -> dict[str, np.ndarray]:
    """
    One Process API request for Sentinel-1 GRD VV and VH (radar backscatter).
    Accepts polygon (GeoJSON), start_date, end_date via time_interval=(start, end).
    Returns dict with "VV" and "VH" (H,W) arrays; invalid/no-data may be 0 or NaN.
    """
    if SentinelHubRequest is None:
        raise RuntimeError("sentinelhub package not installed")
    from shapely.geometry import shape
    geom = shape(geometry_geojson)
    bbox = geom.bounds
    sh_bbox = BBox(bbox, crs=CRS.WGS84)
    w, h = bbox_to_dimensions(sh_bbox, resolution=resolution)
    w, h = max(int(w), min_pixels), max(int(h), min_pixels)
    config = config or _get_config()
    geom_sh = Geometry(geometry_geojson, crs=CRS.WGS84)
    req = SentinelHubRequest(
        evalscript=EVALSCRIPT_S1,
        input_data=_cdse_s1_input(time_interval),
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        geometry=geom_sh,
        size=(w, h),
        config=config,
    )
    try:
        data = req.get_data()
    except Exception as e:
        logger.warning("[S1 SAR] Request failed: %s", e)
        return {"VV": np.full((h, w), np.nan), "VH": np.full((h, w), np.nan)}
    if not data:
        return {"VV": np.full((h, w), np.nan), "VH": np.full((h, w), np.nan)}
    arr = np.array(data[0], dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[:, :, np.newaxis]
    vv = arr[:, :, 0]
    vh = arr[:, :, 1] if arr.shape[2] >= 2 else np.full_like(vv, np.nan)
    valid_vv = np.isfinite(vv) & (vv > 0)
    valid_vh = np.isfinite(vh) & (vh > 0)
    n_valid_vv = int(np.sum(valid_vv))
    n_valid_vh = int(np.sum(valid_vh))
    vv_flat = np.asarray(vv).ravel()
    vh_flat = np.asarray(vh).ravel()
    vv_min = float(np.nanmin(vv_flat)) if np.any(np.isfinite(vv_flat)) else np.nan
    vv_max = float(np.nanmax(vv_flat)) if np.any(np.isfinite(vv_flat)) else np.nan
    vh_min = float(np.nanmin(vh_flat)) if np.any(np.isfinite(vh_flat)) else np.nan
    vh_max = float(np.nanmax(vh_flat)) if np.any(np.isfinite(vh_flat)) else np.nan
    logger.info(
        "[S1 SAR] VV shape=%s valid=%d min=%s max=%s VH shape=%s valid=%d min=%s max=%s",
        vv.shape, n_valid_vv, f"{vv_min:.6f}" if np.isfinite(vv_min) else "nan", f"{vv_max:.6f}" if np.isfinite(vv_max) else "nan",
        vh.shape, n_valid_vh, f"{vh_min:.6f}" if np.isfinite(vh_min) else "nan", f"{vh_max:.6f}" if np.isfinite(vh_max) else "nan",
    )
    return {"VV": vv, "VH": vh}
