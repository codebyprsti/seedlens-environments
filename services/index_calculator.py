"""
Backend index calculation module.

Computes Sentinel-2 derived layers from raw bands (numpy arrays).
Used after fetching raw bands once from Sentinel Process API.
All functions use numpy, handle divide-by-zero safely, and preserve nodata (NaN).
"""

from __future__ import annotations

import numpy as np

# Sentinel-2 L2A band order as returned by sentinel_service (raw bands request):
# 0=B02, 1=B03, 2=B04, 3=B08, 4=B8A, 5=B11, 6=B12, 7=SCL
BAND_INDEX = {"B02": 0, "B03": 1, "B04": 2, "B08": 3, "B8A": 4, "B11": 5, "B12": 6, "SCL": 7}


def _safe_divide(num: np.ndarray, den: np.ndarray, nodata: float = np.nan) -> np.ndarray:
    """Element-wise division; where den is 0 or invalid, return nodata. Preserves dtype of num where possible."""
    den_safe = np.where(den != 0, den, 1)
    out = np.full_like(num, nodata, dtype=np.float64)
    valid = np.isfinite(num) & np.isfinite(den) & (den != 0)
    out[valid] = num[valid] / den[valid]
    return out


def compute_ndvi(b8: np.ndarray, b4: np.ndarray) -> np.ndarray:
    """NDVI = (B08 - B04) / (B08 + B04). Returns float array; invalid pixels are NaN."""
    num = b8.astype(np.float64) - b4.astype(np.float64)
    den = b8.astype(np.float64) + b4.astype(np.float64)
    out = _safe_divide(num, den)
    return np.clip(out, -1.0, 1.0)


def compute_ndwi(b3: np.ndarray, b8: np.ndarray) -> np.ndarray:
    """NDWI = (B03 - B08) / (B03 + B08). Returns float array; invalid pixels are NaN."""
    num = b3.astype(np.float64) - b8.astype(np.float64)
    den = b3.astype(np.float64) + b8.astype(np.float64)
    out = _safe_divide(num, den)
    return np.clip(out, -1.0, 1.0)


def compute_ndsi(b3: np.ndarray, b11: np.ndarray) -> np.ndarray:
    """NDSI (snow) = (B03 - B11) / (B03 + B11). Returns float array; invalid pixels are NaN."""
    num = b3.astype(np.float64) - b11.astype(np.float64)
    den = b3.astype(np.float64) + b11.astype(np.float64)
    out = _safe_divide(num, den)
    return np.clip(out, -1.0, 1.0)


def compute_ndmi(b8a: np.ndarray, b11: np.ndarray) -> np.ndarray:
    """Moisture index NDMI = (B8A - B11) / (B8A + B11). Returns float array; invalid pixels are NaN."""
    num = b8a.astype(np.float64) - b11.astype(np.float64)
    den = b8a.astype(np.float64) + b11.astype(np.float64)
    out = _safe_divide(num, den)
    return np.clip(out, -1.0, 1.0)


def compute_true_color(b4: np.ndarray, b3: np.ndarray, b2: np.ndarray) -> np.ndarray:
    """True color RGB from B04 (R), B03 (G), B02 (B). Returns (H, W, 3) float in [0,1] range.
    Apply scaling (e.g. * 2.5) for display; nodata preserved as NaN."""
    r = np.where(np.isfinite(b4), b4.astype(np.float64), np.nan)
    g = np.where(np.isfinite(b3), b3.astype(np.float64), np.nan)
    b_ = np.where(np.isfinite(b2), b2.astype(np.float64), np.nan)
    out = np.stack([r, g, b_], axis=-1)
    return np.clip(out, 0.0, 1.0)


def compute_swir(b12: np.ndarray, b8a: np.ndarray, b4: np.ndarray) -> np.ndarray:
    """SWIR false color composite: B12 (R), B8A (G), B04 (B). Returns (H, W, 3) float.
    Values typically in [0,1]; nodata preserved as NaN."""
    r = np.where(np.isfinite(b12), b12.astype(np.float64), np.nan)
    g = np.where(np.isfinite(b8a), b8a.astype(np.float64), np.nan)
    b_ = np.where(np.isfinite(b4), b4.astype(np.float64), np.nan)
    out = np.stack([r, g, b_], axis=-1)
    return np.clip(out, 0.0, 1.0)


def compute_layer_from_bands(bands: np.ndarray, layer_name: str) -> np.ndarray:
    """
    Compute a single layer from raw bands array (H, W, 8) with order B02,B03,B04,B08,B8A,B11,B12,SCL.

    Supported layer_name: true_color, ndvi, ndwi, ndsi, moisture_index (ndmi), swir, scl.

    Returns (H, W) for single-band layers or (H, W, 3) for RGB. Invalid/nodata as NaN.
    """
    if bands is None or bands.ndim != 3 or bands.shape[2] < 8:
        raise ValueError("bands must be (H, W, 8) array")
    b02, b03, b04, b08, b8a, b11, b12, scl = (
        bands[:, :, 0], bands[:, :, 1], bands[:, :, 2], bands[:, :, 3],
        bands[:, :, 4], bands[:, :, 5], bands[:, :, 6], bands[:, :, 7],
    )
    name = (layer_name or "").strip().lower()
    if name in ("true_color", "true color", "rgb"):
        return compute_true_color(b04, b03, b02)
    if name == "ndvi":
        return compute_ndvi(b08, b04)
    if name == "ndwi":
        return compute_ndwi(b03, b08)
    if name in ("ndsi", "snow"):
        return compute_ndsi(b03, b11)
    if name in ("moisture_index", "ndmi", "moisture"):
        return compute_ndmi(b8a, b11)
    if name == "swir":
        return compute_swir(b12, b8a, b04)
    if name == "scl":
        return np.where(np.isfinite(scl), scl.astype(np.float64), np.nan)
    raise ValueError(f"Unknown layer_name: {layer_name}. Supported: true_color, ndvi, ndwi, ndsi, moisture_index, swir, scl")
