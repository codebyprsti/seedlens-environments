"""
Pixel-level cloud masking helpers and quality scoring (post-aggregation).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ESA SCL class codes (Sentinel-2 L2A)
SCL_NO_DATA = 0
SCL_SATURATED = 1
SCL_DARK = 2
SCL_SHADOW = 3
SCL_VEGETATION = 4
SCL_BARE = 5
SCL_WATER = 6
SCL_UNCLASSIFIED = 7
SCL_CLOUD_MEDIUM = 8
SCL_CLOUD_HIGH = 9
SCL_CIRRUS = 10
SCL_SNOW = 11


def assess_daily_quality(row: dict[str, Any], *, min_valid_pct: float) -> dict[str, Any]:
    """
    Derive quality fields from harmonized daily row (fractions 0–100).
    """
    valid = float(row.get("valid_pixel_percentage") or 0)
    cloud = float(row.get("cloud_pixel_percentage") or 0)
    shadow = float(row.get("shadow_pixel_percentage") or 0)
    masked = float(row.get("masked_pixel_percentage") or 0)
    scene_cc = row.get("scene_cloud_cover_pct")

    usable = valid >= min_valid_pct
    # Higher score = more clear pixels, fewer clouds/shadows
    quality_score = max(0.0, min(100.0, valid * (1.0 - (cloud + shadow) / 200.0)))

    out = {
        "valid_pixel_percentage": valid,
        "cloud_pixel_percentage": cloud,
        "shadow_pixel_percentage": shadow,
        "masked_pixel_percentage": masked,
        "usable_scene": usable,
        "quality_score": round(quality_score, 2),
        "scene_cloud_cover_pct": scene_cc,
    }
    logger.info(
        "S2 quality date=%s scene_cloud=%s valid=%.1f%% cloud=%.1f%% shadow=%.1f%% "
        "masked=%.1f%% usable=%s score=%.1f",
        row.get("acquisition_date"),
        f"{scene_cc:.1f}" if scene_cc is not None else "n/a",
        valid,
        cloud,
        shadow,
        masked,
        usable,
        quality_score,
    )
    return out


def sufficient_valid_pixels(
    index_rows: list[dict],
    *,
    min_valid_pct: float,
) -> bool:
    """True if any day in chunk has enough clear pixels after masking."""
    for r in index_rows:
        v = r.get("valid_pixel_percentage")
        if v is not None and float(v) >= min_valid_pct:
            return True
    return len(index_rows) > 0 and min_valid_pct <= 0
