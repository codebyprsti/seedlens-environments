"""
Layer statistics from raster arrays (NaN-safe, in-memory).

Used for village-summary CSV: mean, min, max, std_dev, valid_pixel_count
for single-band layers (ndvi, ndwi, moisture_index, scl).
"""

from __future__ import annotations

from typing import Any, Dict

import numpy as np


def compute_layer_statistics(layer_array: np.ndarray) -> Dict[str, Any]:
    """
    Compute statistics over a 2D layer array, ignoring NaN (nodata).

    Args:
        layer_array: (H, W) float array; may contain NaN.

    Returns:
        dict with keys: mean, min, max, std_dev, valid_pixel_count.
        If no valid pixels, mean/min/max/std_dev are None, valid_pixel_count is 0.
    """
    if layer_array is None or layer_array.size == 0:
        return {
            "mean": None,
            "min": None,
            "max": None,
            "std_dev": None,
            "valid_pixel_count": 0,
        }
    if layer_array.ndim != 2:
        # 3D (e.g. RGB) has no single-band stats; caller should not use this for true_color
        return {
            "mean": None,
            "min": None,
            "max": None,
            "std_dev": None,
            "valid_pixel_count": 0,
        }
    arr = np.asarray(layer_array, dtype=np.float64)
    valid = np.isfinite(arr)
    n = int(np.sum(valid))
    if n == 0:
        return {
            "mean": None,
            "min": None,
            "max": None,
            "std_dev": None,
            "valid_pixel_count": 0,
        }
    flat = arr[valid]
    return {
        "mean": float(np.mean(flat)),
        "min": float(np.min(flat)),
        "max": float(np.max(flat)),
        "std_dev": float(np.std(flat)),
        "valid_pixel_count": n,
    }
