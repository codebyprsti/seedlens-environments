"""
Index registry: which indices come from Sentinel Hub evalscript vs backend calculation.

Prefer API when evalscript already computes the same formula (avoid duplicate work).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

import numpy as np


class IndexSource(str, Enum):
    API = "api"
    COMPUTED = "computed"
    HYBRID = "hybrid"  # API preferred, compute if missing


@dataclass(frozen=True)
class IndexSpec:
    name: str
    satellite: str
    source: IndexSource
    required_bands: tuple[str, ...]
    api_evalscript_band: Optional[str] = None  # name in Statistical output


# --- Sentinel-2: Statistical API evalscript already provides these ---
S2_API_INDICES = frozenset({
    "NDVI", "SAVI", "NDMI", "NDRE", "GCI", "PSRI", "MSAVI", "EVI", "NDWI", "GNDVI", "MSI",
})

# --- Sentinel-2: compute when bands available (not in default Statistical evalscript) ---
S2_COMPUTED_INDICES = (
    "LAI",       # from EVI
    "NDWI_GAO",  # NIR + SWIR1
    "NDRE2",     # B06 red edge
    "CIRE",      # chlorophyll index red edge
    "MCARI",     # chlorophyll absorption
    "MNDWI",     # modified NDWI (Green-SWIR1)/(Green+SWIR1) — uses B03, B11
)

S1_API_BANDS = frozenset({"VV", "VH"})
S1_COMPUTED_INDICES = ("VV_dB", "VH_dB", "vh_vv_ratio", "RVI", "cross_pol_ratio")

S3_API_BANDS = frozenset({"S8", "S9"})
S3_COMPUTED_INDICES = ("LST_K", "LST_C", "LST_delta")


def lai_from_evi_scalar(evi: Optional[float]) -> Optional[float]:
    if evi is None or not np.isfinite(evi):
        return None
    return 3.618 * evi - 0.118


def ndwi_gao(nir: Optional[float], swir1: Optional[float]) -> Optional[float]:
    if nir is None or swir1 is None:
        return None
    den = nir + swir1
    if abs(den) < 1e-10:
        return None
    return (nir - swir1) / den


def ndre2(nir: Optional[float], re2: Optional[float]) -> Optional[float]:
    if nir is None or re2 is None:
        return None
    den = nir + re2
    if abs(den) < 1e-10:
        return None
    return (nir - re2) / den


def cire(nir: Optional[float], re1: Optional[float]) -> Optional[float]:
    if nir is None or re1 is None or re1 == 0:
        return None
    return (nir / re1) - 1.0


def mcari(red: Optional[float], green: Optional[float], re1: Optional[float]) -> Optional[float]:
    if red is None or green is None or re1 is None or red == 0:
        return None
    term1 = (re1 - red) - 0.2 * (re1 - green)
    return term1 * (re1 / red)


def mndwi(green: Optional[float], swir1: Optional[float]) -> Optional[float]:
    if green is None or swir1 is None:
        return None
    den = green + swir1
    if abs(den) < 1e-10:
        return None
    return (green - swir1) / den


def rvi(vv: Optional[float], vh: Optional[float]) -> Optional[float]:
    if vv is None or vh is None:
        return None
    den = vv + vh
    if abs(den) < 1e-10:
        return None
    return (4.0 * vh) / den


def cross_pol_ratio(vh: Optional[float], vv: Optional[float]) -> Optional[float]:
    if vh is None or vv is None or vv == 0:
        return None
    return vh / vv


def compute_s2_extras(row: dict) -> tuple[dict, list[str], dict]:
    """
    Fill computed S2 indices from band means dict (keys: b02.. or SENT2_B02).
    Returns (extras, missing_band_names, index_sources).
    """
    def g(*keys: str) -> Optional[float]:
        for k in keys:
            v = row.get(k)
            if v is not None:
                try:
                    f = float(v)
                    return f if np.isfinite(f) else None
                except (TypeError, ValueError):
                    pass
        return None

    b02 = g("b02", "SENT2_B02", "blue")
    b03 = g("b03", "SENT2_B03", "green")
    b04 = g("b04", "SENT2_B04", "red")
    b05 = g("b05", "SENT2_B05", "rededge1")
    b06 = g("b06", "SENT2_B06", "rededge2")
    b08 = g("b08", "SENT2_B08", "nir")
    b11 = g("b11", "SENT2_B11", "swir1")

    missing: list[str] = []
    for name, val in [("B02", b02), ("B03", b03), ("B04", b04), ("B05", b05), ("B06", b06), ("B08", b08), ("B11", b11)]:
        if val is None:
            missing.append(name)

    sources: dict = {}
    out: dict = {}

    evi = row.get("EVI") or row.get("evi")
    if evi is not None:
        sources["evi"] = "api"
    lai = lai_from_evi_scalar(float(evi) if evi is not None else None)
    if lai is not None:
        out["lai"] = lai
        sources["lai"] = "computed_from_evi"

    ng = ndwi_gao(b08, b11)
    if ng is not None:
        out["ndwi_gao"] = ng
        sources["ndwi_gao"] = "computed"

    n2 = ndre2(b08, b06)
    if n2 is not None:
        out["ndre2"] = n2
        sources["ndre2"] = "computed"

    ci = cire(b08, b05)
    if ci is not None:
        out["cire"] = ci
        sources["cire"] = "computed"

    mc = mcari(b04, b03, b05)
    if mc is not None:
        out["mcari"] = mc
        sources["mcari"] = "computed"

    mn = mndwi(b03, b11)
    if mn is not None:
        out["mndwi"] = mn
        sources["mndwi"] = "computed"

    return out, missing, sources


def merge_api_index_sources(obs: dict) -> dict:
    """Mark indices present in Statistical API response."""
    src = {}
    for key in S2_API_INDICES:
        v = obs.get(key)
        if v is not None:
            src[key.lower()] = "api"
    if obs.get("LAI") is not None:
        src["lai"] = "api"
    return src
