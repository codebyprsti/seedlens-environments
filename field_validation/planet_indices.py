"""
PlanetScope-aligned index formulas (reuse crop_monitoring.index_calculator for S2 parity).

GNDVI: (NIR − Green) / (NIR + Green). NDMI requires NIR + SWIR — not available on 4-band analytic.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from crop_monitoring.index_calculator import evi, ndmi, ndre, ndvi, savi


def gndvi(nir: np.ndarray, green: np.ndarray) -> np.ndarray:
    """Green NDVI: (NIR - Green) / (NIR + Green), clipped [-1, 1]."""
    n = nir.astype(np.float64) - green.astype(np.float64)
    d = nir.astype(np.float64) + green.astype(np.float64)
    out = np.full_like(n, np.nan, dtype=np.float64)
    valid = np.isfinite(n) & np.isfinite(d) & (d != 0)
    out[valid] = np.clip(n[valid] / d[valid], -1.0, 1.0)
    return out


def scalar_ndvi(nir: float, red: float) -> Optional[float]:
    """NDVI = (NIR - Red) / (NIR + Red) for verification; None if invalid."""
    den = nir + red
    if den == 0 or not np.isfinite(den):
        return None
    v = (nir - red) / den
    return float(np.clip(v, -1.0, 1.0))


def compute_indices_from_bgrn_means(
    blue: float,
    green: float,
    red: float,
    nir: float,
    *,
    red_edge: Optional[float] = None,
    swir: Optional[float] = None,
) -> dict[str, Any]:
    """
    Zonal means as 0-D arrays → index means. Aligns with crop_monitoring formulas.
    NDMI only if swir provided. NDRE only if red_edge provided.
    """
    b = np.array([[blue]], dtype=np.float64)
    g = np.array([[green]], dtype=np.float64)
    r = np.array([[red]], dtype=np.float64)
    n = np.array([[nir]], dtype=np.float64)
    out: dict[str, Any] = {
        "planet_ndvi_mean": float(np.nanmean(ndvi(n, r))),
        "planet_savi_mean": float(np.nanmean(savi(n, r))),
        "planet_evi_mean": float(np.nanmean(evi(n, r, b))),
        "planet_gndvi_mean": float(np.nanmean(gndvi(n, g))),
        "planet_ndre_mean": None,
        "planet_ndmi_mean": None,
    }
    if red_edge is not None and np.isfinite(red_edge):
        re = np.array([[red_edge]], dtype=np.float64)
        out["planet_ndre_mean"] = float(np.nanmean(ndre(n, re)))
    else:
        out["planet_ndre_note"] = "RedEdge band not in stack (need 8-band or RedEdge product)"
    if swir is not None and np.isfinite(swir):
        s = np.array([[swir]], dtype=np.float64)
        out["planet_ndmi_mean"] = float(np.nanmean(ndmi(n, s)))
    else:
        out["planet_ndmi_note"] = (
            "NDMI requires NIR+SWIR; standard PlanetScope 4-band analytic has no SWIR"
        )
    return out
