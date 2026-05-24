"""
Layer 4: Analytics / output — optional views and legacy crop_indices mirror (disabled by default).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def mirror_to_crop_indices_enabled() -> bool:
    return os.environ.get("SATELLITE_MIRROR_CROP_INDICES", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def build_crop_indices_legacy_row(
    s2: dict[str, Any],
    s1: dict[str, Any] | None,
    s3: dict[str, Any] | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    Map v2 satellite rows to legacy crop_indices insert shape (optional dual-write).
    """
    row = {
        "file_name": context.get("file_name"),
        "analysis_date": s2.get("acquisition_date"),
        "location_id": context.get("location_id"),
        "season_id": context.get("season_id"),
        "grower_id": context.get("grower_id"),
        "variety_id": context.get("variety_id"),
        "polygon_id": context.get("polygon_id"),
        "polygon_area": context.get("polygon_area"),
        "date_start": s2.get("acquisition_date"),
        "date_end": s2.get("acquisition_date"),
        "blue": s2.get("blue") or s2.get("b02"),
        "green": s2.get("green") or s2.get("b03"),
        "red": s2.get("red") or s2.get("b04"),
        "rededge1": s2.get("rededge1") or s2.get("b05"),
        "rededge2": s2.get("rededge2") or s2.get("b06"),
        "rededge3": s2.get("rededge3") or s2.get("b07"),
        "nir": s2.get("nir") or s2.get("b08"),
        "narrow_nir": s2.get("narrow_nir") or s2.get("b8a"),
        "swir1": s2.get("swir1") or s2.get("b11"),
        "swir2": s2.get("swir2") or s2.get("b12"),
        "ndvi": s2.get("ndvi"),
        "savi": s2.get("savi"),
        "ndmi": s2.get("ndmi"),
        "ndre": s2.get("ndre"),
        "gci": s2.get("gci"),
        "psri": s2.get("psri"),
        "msavi": s2.get("msavi"),
        "evi": s2.get("evi"),
        "lai": s2.get("lai"),
        "ndwi": s2.get("ndwi"),
        "ndwi_gao": s2.get("ndwi_gao"),
    }
    if s1:
        row["vv_db"] = s1.get("vv_db")
        row["vh_db"] = s1.get("vh_db")
        row["vh_vv_ratio"] = s1.get("vh_vv_ratio")
    if s3:
        row["lst_celsius"] = s3.get("lst_celsius")
    return row
