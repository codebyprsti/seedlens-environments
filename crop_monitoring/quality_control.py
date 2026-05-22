"""
Validate index values and flag outliers before DB insert.

Document and scientific ranges: NDVI/SAVI/NDMI etc. in [-1, 1];
LST in reasonable Earth surface range.
"""

from __future__ import annotations

from typing import Any

# Expected ranges for indices (document / literature)
INDEX_RANGES: dict[str, tuple[float, float]] = {
    "NDVI": (-1.0, 1.0),
    "SAVI": (-1.0, 1.0),
    "NDMI": (-1.0, 1.0),
    "MSAVI": (-1.0, 1.0),
    "NDRE": (-1.0, 1.0),
    "EVI": (-1.0, 1.0),
    "GCI": (-1.0, 50.0),   # can be high for dense vegetation
    "PSRI": (-2.0, 2.0),
    "LAI": (0.0, 10.0),
    "LST_C": (-50.0, 60.0),
}


def validate_indices(means: dict[str, float]) -> tuple[bool, list[str]]:
    """
    Check index values against expected ranges.
    Returns (all_valid, list of warning messages).
    """
    warnings: list[str] = []
    for key, (low, high) in INDEX_RANGES.items():
        v = means.get(key)
        if v is None:
            continue
        try:
            vf = float(v)
        except (TypeError, ValueError):
            warnings.append(f"{key}={v!r} not numeric")
            continue
        if not (low <= vf <= high):
            warnings.append(f"{key}={vf} outside [{low},{high}]")
    return len(warnings) == 0, warnings


def flag_outliers(
    means: dict[str, float],
    *,
    ndvi_low: float = -0.2,
    ndvi_high: float = 1.0,
) -> list[str]:
    """Optional: flag likely bad data (e.g. NDVI always negative for vegetation)."""
    flags: list[str] = []
    ndvi = means.get("NDVI")
    if ndvi is not None and isinstance(ndvi, (int, float)):
        if ndvi < ndvi_low:
            flags.append(f"NDVI={ndvi} below typical vegetation threshold {ndvi_low}")
        if ndvi > ndvi_high:
            flags.append(f"NDVI={ndvi} above expected max {ndvi_high}")
    return flags
