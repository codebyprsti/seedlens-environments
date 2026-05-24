"""
Extract raw Sentinel-2 band data for farm polygons from KML using SentinelHub Process API.

Reads a KML file, extracts polygon coordinates, converts to Shapely polygon,
and requests bands B02, B03, B04, B08, B11 from Copernicus (one API call).
No vegetation indices—raw bands only for backend computation.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np

try:
    from shapely.geometry import Polygon
except ImportError:
    Polygon = None

try:
    from sentinelhub import (
        BBox,
        CRS,
        DataCollection,
        Geometry,
        MimeType,
        MosaickingOrder,
        SHConfig,
        SentinelHubRequest,
    )
    from sentinelhub.geo_utils import bbox_to_dimensions
    from sentinelhub.time_utils import parse_time_interval, serialize_time
except ImportError:
    SHConfig = None
    SentinelHubRequest = None

# KML 2.2 namespace
KML_NS = "http://www.opengis.net/kml/2.2"

# Bands to request (raw only)
BAND_NAMES = ["B02", "B03", "B04", "B08", "B11"]

# Evalscript: output exactly these 5 bands, no indices
EVALSCRIPT_RAW_BANDS = """
//VERSION=3
function setup() {
  return {
    input: ["B02", "B03", "B04", "B08", "B11"],
    output: { bands: 5, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [
    sample.B02,
    sample.B03,
    sample.B04,
    sample.B08,
    sample.B11
  ];
}
"""

# CDSE (Copernicus Data Space Ecosystem)
CDSE_BASE_URL = "https://sh.dataspace.copernicus.eu"
CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_DEFAULT_SH_CLIENT_ID = os.environ.get("SH_CLIENT_ID", "")
_DEFAULT_SH_CLIENT_SECRET = os.environ.get("SH_CLIENT_SECRET", "")


def _parse_kml_coordinates(kml_path: str | Path) -> list[tuple[float, float]]:
    """Read KML and return first polygon ring as list of (lon, lat)."""
    path = Path(kml_path)
    if not path.exists():
        raise FileNotFoundError(f"KML file not found: {kml_path}")
    tree = ET.parse(path)
    root = tree.getroot()
    # Support default namespace
    coords_elem = root.find(f".//{{{KML_NS}}}coordinates")
    if coords_elem is None:
        # Try without namespace for some KML variants
        for elem in root.iter():
            if elem.tag.endswith("coordinates") or elem.tag == "coordinates":
                coords_elem = elem
                break
    if coords_elem is None or coords_elem.text is None:
        raise ValueError("No <coordinates> element found in KML")
    text = coords_elem.text.strip()
    points: list[tuple[float, float]] = []
    for line in text.split():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) >= 2:
            lon = float(parts[0])
            lat = float(parts[1])
            points.append((lon, lat))
    if len(points) < 3:
        raise ValueError("KML polygon must have at least 3 points")
    # Close ring if not closed
    if points[0] != points[-1]:
        points.append(points[0])
    return points


def kml_to_polygon(kml_path: str | Path):
    """Read KML and return a Shapely Polygon (WGS84)."""
    if Polygon is None:
        raise RuntimeError("shapely is required; install with: pip install shapely")
    coords = _parse_kml_coordinates(kml_path)
    return Polygon(coords)


def _get_config() -> Any:
    if SHConfig is None:
        raise RuntimeError("sentinelhub package not installed")
    config = SHConfig()
    config.sh_client_id = os.environ.get("SH_CLIENT_ID") or _DEFAULT_SH_CLIENT_ID
    config.sh_client_secret = os.environ.get("SH_CLIENT_SECRET") or _DEFAULT_SH_CLIENT_SECRET
    config.sh_base_url = CDSE_BASE_URL
    config.sh_token_url = CDSE_TOKEN_URL
    if os.environ.get("SH_INSTANCE_ID"):
        config.instance_id = os.environ["SH_INSTANCE_ID"]
    return config


def last_n_days_range(days: int = 5) -> tuple[str, str]:
    """Return (start_date, end_date) for the last N days up to present (today UTC)."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(1, days))
    return start.isoformat(), end.isoformat()


def _cdse_input_data(time_interval: tuple[str, str], maxcc: float = 20) -> list[dict]:
    start_time, end_time = serialize_time(
        parse_time_interval(time_interval, allow_undefined=True), use_tz=True
    )
    maxcc_pct = int(maxcc) if maxcc > 1 else int(maxcc * 100)
    return [
        {
            "type": DataCollection.SENTINEL2_L2A.api_id,
            "dataFilter": {
                "timeRange": {"from": start_time, "to": end_time},
                "maxCloudCoverage": maxcc_pct,
                "mosaickingOrder": MosaickingOrder.LEAST_CC.value,
            },
        }
    ]


def get_sentinel_bands(
    kml_path: str | Path,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    *,
    config: Optional[Any] = None,
    maxcc: float = 20,
    resolution: int = 10,
    min_pixels: int = 64,
    last_days: Optional[int] = None,
) -> dict[str, np.ndarray]:
    """
    Extract raw Sentinel-2 bands (B02, B03, B04, B08, B11) for the polygon in a KML file.

    Uses a single SentinelHub Process API request. Returns raw band arrays only;
    no vegetation indices are computed.

    Args:
        kml_path: Path to the KML file containing the farm polygon.
        start_date: Start of date range (e.g. "2025-01-01"). If omitted, uses last_days or last 5 days.
        end_date: End of date range (e.g. "2025-01-31"). If omitted, uses today (UTC).
        config: Optional SHConfig; otherwise uses env SH_CLIENT_ID / SH_CLIENT_SECRET.
        maxcc: Maximum cloud coverage 0–100 (default 20).
        resolution: Output resolution in metres (default 10).
        min_pixels: Minimum width/height in pixels (default 64).
        last_days: If set, ignore start_date/end_date and use last N days up to present (default 5 when dates omitted).

    Returns:
        Dictionary mapping band name to 2D float32 array, e.g.:
        {"B02": array(H,W), "B03": array(H,W), "B04": array(H,W), "B08": array(H,W), "B11": array(H,W)}.

    Raises:
        FileNotFoundError: If KML file does not exist.
        ValueError: If KML has no valid polygon coordinates.
        RuntimeError: If sentinelhub is not installed or API returns no data.

    Example (last 5 days up to present):
        bands = get_sentinel_bands("farm.kml")  # last 5 days to today
        bands = get_sentinel_bands("farm.kml", last_days=5)  # same

    Note on timing: Sentinel-2 revisit is ~5 days; L2A processing adds 1–3+ days delay.
    "Last 5 days" returns the best available scene(s) in that window (often 1 acquisition);
    "current date" alone often has no new scene yet, so last_days=5 is the practical way
    to get the most recent available data.
    """
    if SentinelHubRequest is None:
        raise RuntimeError("sentinelhub package not installed")
    if last_days is not None:
        start_date, end_date = last_n_days_range(last_days)
    elif start_date is None or end_date is None:
        start_date, end_date = last_n_days_range(5)
    time_interval = (start_date, end_date)
    polygon = kml_to_polygon(kml_path)
    geojson = polygon.__geo_interface__
    config = config or _get_config()

    bbox = polygon.bounds  # (minx, miny, maxx, maxy) = (min_lon, min_lat, max_lon, max_lat)
    sh_bbox = BBox(bbox, crs=CRS.WGS84)
    w, h = bbox_to_dimensions(sh_bbox, resolution=resolution)
    w = max(int(w), min_pixels)
    h = max(int(h), min_pixels)

    geom = Geometry(geojson, crs=CRS.WGS84)
    request = SentinelHubRequest(
        evalscript=EVALSCRIPT_RAW_BANDS,
        input_data=_cdse_input_data(time_interval, maxcc),
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        geometry=geom,
        size=(w, h),
        config=config,
    )
    data = request.get_data()
    if not data:
        raise RuntimeError("Sentinel Hub returned no data for the given polygon and date range")
    arr = np.array(data[0], dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[:, :, np.newaxis]
    if arr.shape[2] != len(BAND_NAMES):
        raise RuntimeError(f"Expected {len(BAND_NAMES)} bands, got {arr.shape[2]}")

    return {name: arr[:, :, i] for i, name in enumerate(BAND_NAMES)}
