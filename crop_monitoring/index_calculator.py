"""
Compute vegetation, moisture, and derived indices from Sentinel-2 bands.
Vectorized numpy; invalid/missing values produce NaN.
"""

from __future__ import annotations

import numpy as np


def _safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    out = np.full_like(num, np.nan, dtype=np.float64)
    valid = np.isfinite(num) & np.isfinite(den) & (den != 0)
    out[valid] = num[valid] / den[valid]
    return out


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """NDVI = (NIR - Red) / (NIR + Red)."""
    n = nir.astype(np.float64) - red.astype(np.float64)
    d = nir.astype(np.float64) + red.astype(np.float64)
    return np.clip(_safe_div(n, d), -1.0, 1.0)


def savi(nir: np.ndarray, red: np.ndarray, L: float = 0.5) -> np.ndarray:
    """SAVI = ((NIR - Red) / (NIR + Red + L)) * (1 + L). With L=0.5: * 1.5."""
    n = nir.astype(np.float64) - red.astype(np.float64)
    d = nir.astype(np.float64) + red.astype(np.float64) + L
    return (1.0 + L) * _safe_div(n, d)


def ndmi(nir: np.ndarray, swir: np.ndarray) -> np.ndarray:
    """NDMI = (NIR - SWIR) / (NIR + SWIR)."""
    n = nir.astype(np.float64) - swir.astype(np.float64)
    d = nir.astype(np.float64) + swir.astype(np.float64)
    return np.clip(_safe_div(n, d), -1.0, 1.0)


def ndre(nir: np.ndarray, red_edge: np.ndarray) -> np.ndarray:
    """NDRE = (NIR - RedEdge) / (NIR + RedEdge)."""
    n = nir.astype(np.float64) - red_edge.astype(np.float64)
    d = nir.astype(np.float64) + red_edge.astype(np.float64)
    return np.clip(_safe_div(n, d), -1.0, 1.0)


def gci(nir: np.ndarray, green: np.ndarray) -> np.ndarray:
    """GCI = (NIR / Green) - 1."""
    return _safe_div(nir.astype(np.float64), np.where(green != 0, green.astype(np.float64), np.nan)) - 1.0


def psri(red: np.ndarray, green: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """PSRI = (Red - Green) / NIR."""
    num = red.astype(np.float64) - green.astype(np.float64)
    return _safe_div(num, nir.astype(np.float64))


def msavi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    """MSAVI = (2*NIR + 1 - sqrt((2*NIR+1)^2 - 8*(NIR - Red))) / 2."""
    nir = nir.astype(np.float64)
    red = red.astype(np.float64)
    two_nir_1 = 2.0 * nir + 1.0
    discriminant = two_nir_1 ** 2 - 8.0 * (nir - red)
    sqrt_d = np.where(discriminant >= 0, np.sqrt(discriminant), np.nan)
    out = (two_nir_1 - sqrt_d) / 2.0
    return np.where(np.isfinite(out), np.clip(out, -1.0, 1.0), np.nan)


def evi(nir: np.ndarray, red: np.ndarray, blue: np.ndarray, G: float = 2.5, C1: float = 6.0, C2: float = 7.5) -> np.ndarray:
    """EVI = G * (NIR - Red) / (NIR + C1*Red - C2*Blue + 1). Default 2.5 * (NIR-Red)/(NIR+6*Red-7.5*Blue+1)."""
    n = nir.astype(np.float64) - red.astype(np.float64)
    d = nir.astype(np.float64) + C1 * red.astype(np.float64) - C2 * blue.astype(np.float64) + 1.0
    return G * _safe_div(n, d)


def lai_from_evi(evi_arr: np.ndarray, a: float = 3.618, b: float = -0.118) -> np.ndarray:
    """LAI = a * EVI + b. Default 3.618*EVI - 0.118."""
    return np.where(np.isfinite(evi_arr), a * evi_arr + b, np.nan)


def compute_all_indices(
    B02: np.ndarray, B03: np.ndarray, B04: np.ndarray, B05: np.ndarray,
    B08: np.ndarray, B11: np.ndarray,
) -> dict[str, np.ndarray]:
    """Compute NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, LAI from S2 bands. Mask invalid (<=0 or non-finite) before mean."""
    evi_arr = evi(B08, B04, B02)
    lai_arr = lai_from_evi(evi_arr)
    return {
        "NDVI": ndvi(B08, B04),
        "SAVI": savi(B08, B04),
        "NDMI": ndmi(B08, B11),
        "NDRE": ndre(B08, B05),
        "GCI": gci(B08, B03),
        "PSRI": psri(B04, B03, B08),
        "MSAVI": msavi(B08, B04),
        "EVI": evi_arr,
        "LAI": lai_arr,
    }
