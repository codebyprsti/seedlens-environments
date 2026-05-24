"""
Column naming aligned with operations.crop_indices (see sql/migrate_crop_indices_field_locations_alignment.sql).
"""

from __future__ import annotations

import math
from typing import Any, Optional

# Sentinel-2 L2A internal id → crop_indices column
S2_BAND_TO_CROP: dict[str, str] = {
    "b01": "coastal",
    "b02": "blue",
    "b03": "green",
    "b04": "red",
    "b05": "rededge1",
    "b06": "rededge2",
    "b07": "rededge3",
    "b08": "nir",
    "b8a": "narrow_nir",
    "b09": "cirrus",
    "b11": "swir1",
    "b12": "swir2",
    # Uppercase / legacy keys
    "B01": "coastal",
    "B02": "blue",
    "B03": "green",
    "B04": "red",
    "B05": "rededge1",
    "B06": "rededge2",
    "B07": "rededge3",
    "B08": "nir",
    "B8A": "narrow_nir",
    "B09": "cirrus",
    "B11": "swir1",
    "B12": "swir2",
}

S2_CROP_TO_BAND: dict[str, str] = {v: k for k, v in S2_BAND_TO_CROP.items() if k == k.lower()}

# crop_indices SAR columns (dB) + linear scatter stored separately on sentinel1
S1_CROP_SAR_DB_COLS = ("vv_db", "vh_db", "vh_vv_ratio")
S1_LINEAR_COLS = ("vv", "vh")


def is_likely_db(value: float) -> bool:
    """Sentinel-1 dB backscatter is negative; linear sigma0 is in (0, ~1]."""
    return value < 0


def db_to_linear(db: Optional[float]) -> Optional[float]:
    if db is None:
        return None
    try:
        v = float(db)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return 10.0 ** (v / 10.0)


def linear_to_db(linear: Optional[float]) -> Optional[float]:
    if linear is None:
        return None
    try:
        v = float(linear)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return 10.0 * math.log10(v)


def normalize_s1_scatter_fields(row: dict[str, Any]) -> dict[str, Any]:
    """
    Ensure vv/vh = linear backscatter, vv_db/vh_db = dB (crop_indices convention).
    Fixes rows where dB was stored in vv/vh or only dB columns populated.
    """
    out = dict(row)
    vv = out.get("vv")
    vh = out.get("vh")
    vv_db = out.get("vv_db")
    vh_db = out.get("vh_db")

    if vv is not None and is_likely_db(float(vv)):
        vv_db = vv_db if vv_db is not None else float(vv)
        vv = db_to_linear(vv_db)
    elif vv is None and vv_db is not None:
        vv = db_to_linear(vv_db)

    if vh is not None and is_likely_db(float(vh)):
        vh_db = vh_db if vh_db is not None else float(vh)
        vh = db_to_linear(vh_db)
    elif vh is None and vh_db is not None:
        vh = db_to_linear(vh_db)

    if vv is not None and vv > 0:
        vv_db = linear_to_db(vv) if vv_db is None else vv_db
    if vh is not None and vh > 0:
        vh_db = linear_to_db(vh) if vh_db is None else vh_db

    if vv is not None and vh is not None and vv > 0:
        out["vh_vv_ratio"] = out.get("vh_vv_ratio") if out.get("vh_vv_ratio") is not None else vh / vv

    out["vv"] = vv
    out["vh"] = vh
    out["vv_db"] = vv_db
    out["vh_db"] = vh_db
    return out


def map_s2_bands_to_crop(row: dict[str, Any]) -> dict[str, Any]:
    """Copy b02-style keys to crop_indices names (blue, green, …)."""
    out = dict(row)
    for src, dst in S2_BAND_TO_CROP.items():
        if src == src.upper():
            continue
        if out.get(dst) is None and out.get(src) is not None:
            out[dst] = out[src]
    return out
