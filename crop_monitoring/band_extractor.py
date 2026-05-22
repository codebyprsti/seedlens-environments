"""
Fetch Sentinel-2, Sentinel-3, and Sentinel-1 SAR band data for a KML polygon.
Uses sentinel_client: S2 (B02–B05, B08, B11, B12), S3 (S8,S9), S1 (VV,VH).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np

from crop_monitoring.kml_parser import parse_kml
from crop_monitoring.sentinel_client import fetch_s2_bands, fetch_s3_thermal, fetch_s1_sar

# S1 revisit is ~6–12 days; use wider window to improve chance of a scene
S1_DAYS_BACK = 90


def fetch_band_data(
    kml_path: str | Path,
    start_date: str,
    end_date: str,
    *,
    config: Optional[Any] = None,
    maxcc: float = 20,
    resolution_s2: int = 10,
    fetch_thermal: bool = True,
    fetch_sar: bool = True,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray], dict]:
    """
    Load KML, fetch S2 bands, optionally S3 thermal, and optionally S1 SAR.

    Returns:
        s2_bands: dict B02, B03, B04, B05, B08, B11, B12 -> (H,W) array
        s3_bands: dict S8, S9 -> (H,W) array (Kelvin), or empty if fetch_thermal=False/failed
        s1_bands: dict VV, VH -> (H,W) array, or empty if fetch_sar=False/failed
        geojson: polygon GeoJSON for area
    """
    geojson, _ = parse_kml(kml_path)
    time_interval = (start_date, end_date)
    s2_bands = fetch_s2_bands(geojson, time_interval, config=config, maxcc=maxcc, resolution=resolution_s2)
    s3_bands = {}
    if fetch_thermal:
        try:
            s3_bands = fetch_s3_thermal(geojson, time_interval, config=config)
        except Exception:
            pass
    s1_bands = {}
    if fetch_sar:
        try:
            # S1 has fewer acquisitions; use wider window (e.g. 90 days) to improve chance of a scene
            end_str = end_date[:10] if len(end_date) >= 10 else end_date
            end_dt = datetime.strptime(end_str, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=S1_DAYS_BACK)
            s1_interval = (start_dt.strftime("%Y-%m-%d"), end_str)
            s1_bands = fetch_s1_sar(geojson, s1_interval, config=config, resolution=resolution_s2)
        except Exception:
            pass
    return s2_bands, s3_bands, s1_bands, geojson
