"""
Sentinel-1 SAR metrics: convert backscatter to dB and compute VH/VV ratio.
Safe division and NaN handling; invalid values (<=0) produce NaN.
"""

from __future__ import annotations

import numpy as np


def _safe_log10(arr: np.ndarray) -> np.ndarray:
    """log10 where arr > 0 and finite; else NaN."""
    arr = np.asarray(arr, dtype=np.float64)
    out = np.full_like(arr, np.nan)
    valid = np.isfinite(arr) & (arr > 0)
    out[valid] = np.log10(arr[valid])
    return out


def compute_vv_db(vv: np.ndarray) -> np.ndarray:
    """VV_dB = 10 * log10(VV). Invalid/zero/negative -> NaN."""
    return 10.0 * _safe_log10(vv)


def compute_vh_db(vh: np.ndarray) -> np.ndarray:
    """VH_dB = 10 * log10(VH). Invalid/zero/negative -> NaN."""
    return 10.0 * _safe_log10(vh)


def compute_vh_vv_ratio(vh: np.ndarray, vv: np.ndarray) -> np.ndarray:
    """ratio = VH / VV. Safe division: VV<=0 or invalid -> NaN."""
    vh = np.asarray(vh, dtype=np.float64)
    vv = np.asarray(vv, dtype=np.float64)
    out = np.full_like(vv, np.nan)
    valid = np.isfinite(vv) & np.isfinite(vh) & (vv > 0)
    out[valid] = vh[valid] / vv[valid]
    return out


def compute_sar_metrics(vv: np.ndarray, vh: np.ndarray) -> dict[str, np.ndarray]:
    """
    Compute VV_dB, VH_dB, and VH/VV ratio arrays from VV and VH backscatter.
    Returns dict with keys "VV_dB", "VH_dB", "vh_vv_ratio".
    """
    return {
        "VV_dB": compute_vv_db(vv),
        "VH_dB": compute_vh_db(vh),
        "vh_vv_ratio": compute_vh_vv_ratio(vh, vv),
    }
