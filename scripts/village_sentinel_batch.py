#!/usr/bin/env python3
"""
Batch process villages (Medchal-Malkajgiri, Telangana) with Sentinel Hub:
NDVI, NDWI, crop health metrics, per-village statistics, weekly time-series,
and QGIS-ready exports.

- Loads village GeoJSON from file (district: Medchal-Malkajgiri).
- Extracts multiple layers (NDVI, NDWI, GNDVI/crop_health, EVI, NDBI, etc.); use --layers to choose.
- Outputs per-village statistics per layer (mean_ndvi, mean_ndwi, mean_evi, …) and cloud-free %, area (ha).
- Exports one raster per layer per village and QGIS-ready GeoJSON with all layer stats as attributes.

Requires: sentinelhub, numpy.
Optional: shapely+pyproj for geodesic area_ha (else bbox approx); rasterio to write georeferenced GeoTIFF from array response.
To export real village GeoJSON from DB: query operations.location_polygons (and operations.locations) for district 'Medchal-Malkajgiri', build FeatureCollection with polygon_geojson or polygon_geom (ST_AsGeoJSON).

Usage:
    python scripts/village_sentinel_batch.py --geojson data/villages_medchal_malkajgiri.geojson
    python scripts/village_sentinel_batch.py --geojson data/villages.geojson --max-villages 10 --year 2026
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from sentinelhub import (
        CRS,
        BBox,
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
    print("Install sentinelhub: pip install sentinelhub", file=sys.stderr)
    sys.exit(1)

# Optional: geodesic area
try:
    from shapely.geometry import shape
    from shapely.ops import transform
    import pyproj
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Evalscripts
# ---------------------------------------------------------------------------

EVALSCRIPT_NDVI_CLOUD = """
//VERSION=3
function setup() {
  return {
    input: ["B04", "B08", "SCL"],
    output: { bands: 2, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  let ndvi = (sample.B08 - sample.B04) / (sample.B08 + sample.B04 + 1e-8);
  ndvi = Math.max(-1, Math.min(1, ndvi));
  // SCL: 0=nodata, 1=def cloud, 2=cloud shadows, 3=veg, 4=bare, 5=water, 6=unclassified, 7=snow, 8=cloud med, 9=cloud high, 10=thin cloud, 11=snow shadow
  let cloud = (sample.SCL >= 8 && sample.SCL <= 10) || sample.SCL === 1 ? 1 : 0;
  return [ndvi, cloud];
}
"""

EVALSCRIPT_NDWI = """
//VERSION=3
function setup() {
  return {
    input: ["B03", "B08"],
    output: { bands: 1, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  let ndwi = (sample.B03 - sample.B08) / (sample.B03 + sample.B08 + 1e-8);
  return [Math.max(-1, Math.min(1, ndwi))];
}
"""

# GNDVI as crop health (green-normalized difference)
EVALSCRIPT_CROP_HEALTH = """
//VERSION=3
function setup() {
  return {
    input: ["B03", "B08", "SCL"],
    output: { bands: 1, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  let gndvi = (sample.B08 - sample.B03) / (sample.B08 + sample.B03 + 1e-8);
  let cloud = (sample.SCL >= 8 && sample.SCL <= 10) || sample.SCL === 1;
  if (sample.SCL >= 3 && sample.SCL <= 7 && !cloud)
    return [Math.max(-1, Math.min(1, gndvi))];
  return [NaN];
}
"""

# EVI (Enhanced Vegetation Index) – better in high biomass
EVALSCRIPT_EVI = """
//VERSION=3
function setup() {
  return {
    input: ["B02", "B04", "B08", "SCL"],
    output: { bands: 1, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  let cloud = (sample.SCL >= 8 && sample.SCL <= 10) || sample.SCL === 1;
  if (cloud || sample.SCL < 3 || sample.SCL > 7) return [NaN];
  let evi = 2.5 * (sample.B08 - sample.B04) / (sample.B08 + 6 * sample.B04 - 7.5 * sample.B02 + 1);
  return [Math.max(-1, Math.min(1, evi))];
}
"""

# NDBI (Normalized Difference Built-up Index) – built-up vs vegetation, band 11 (SWIR)
EVALSCRIPT_NDBI = """
//VERSION=3
function setup() {
  return {
    input: ["B08", "B11", "SCL"],
    output: { bands: 1, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  let cloud = (sample.SCL >= 8 && sample.SCL <= 10) || sample.SCL === 1;
  if (cloud || sample.SCL < 3 || sample.SCL > 7) return [NaN];
  let ndbi = (sample.B11 - sample.B08) / (sample.B11 + sample.B08 + 1e-8);
  return [Math.max(-1, Math.min(1, ndbi))];
}
"""

# Layer registry: id, display name, evalscript, stat_band (0-based; "ndvi_cloud" = use compute_stats_from_ndvi_cloud)
# Add or remove layers here to extract different indices; all get exported as raster and (where applicable) stats in JSON/GeoJSON.
LAYERS = [
    {"id": "ndvi", "name": "NDVI", "evalscript": EVALSCRIPT_NDVI_CLOUD, "stat": "ndvi_cloud"},
    {"id": "ndwi", "name": "NDWI", "evalscript": EVALSCRIPT_NDWI, "stat_band": 0},
    {"id": "crop_health", "name": "GNDVI", "evalscript": EVALSCRIPT_CROP_HEALTH, "stat_band": 0},
    {"id": "evi", "name": "EVI", "evalscript": EVALSCRIPT_EVI, "stat_band": 0},
    {"id": "ndbi", "name": "NDBI", "evalscript": EVALSCRIPT_NDBI, "stat_band": 0},
]

# ---------------------------------------------------------------------------
# GeoJSON load and filter
# ---------------------------------------------------------------------------

def load_village_geojson(path: str, district_filter: str = "Medchal-Malkajgiri", max_features: int = 10):
    """Load GeoJSON from file and return FeatureCollection with district filter and limit."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"GeoJSON file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("type") == "FeatureCollection":
        features = data.get("features", [])
    elif data.get("type") == "Feature":
        features = [data]
    else:
        raise ValueError("GeoJSON must be FeatureCollection or Feature")
    district_lower = district_filter.lower().strip()
    filtered = []
    for f in features:
        props = f.get("properties") or {}
        dist = (props.get("district") or "").lower().strip()
        if dist == district_lower or district_lower in dist:
            filtered.append(f)
    limited = filtered[:max_features]
    return {"type": "FeatureCollection", "features": limited}


def polygon_area_ha(geom: dict) -> float:
    """Compute polygon area in hectares. Uses shapely+pyproj if available, else bbox approximation."""
    if HAS_SHAPELY and geom.get("type") == "Polygon":
        try:
            shp = shape(geom)
            wgs84 = pyproj.CRS("EPSG:4326")
            # UTM 43N covers Telangana/Hyderabad
            utm = pyproj.CRS("EPSG:32643")
            project = pyproj.Transformer.from_crs(wgs84, utm, always_xy=True).transform
            shp_utm = transform(project, shp)
            return shp_utm.area / 10000.0  # m² -> ha
        except Exception as e:
            logger.warning("Shapely area failed: %s; using bbox approx", e)
    # Bbox approximation at ~17.5°N
    coords = geom.get("coordinates")
    if not coords or geom.get("type") != "Polygon":
        return 0.0
    ring = coords[0]
    lats = [p[1] for p in ring]
    lons = [p[0] for p in ring]
    lat_span = max(lats) - min(lats)
    lon_span = max(lons) - min(lons)
    # 1 deg lat ≈ 111 km; 1 deg lon ≈ 106 km at 17.5°N
    m_per_deg_lat = 111320
    m_per_deg_lon = 111320 * 0.96  # cos(17.5°)
    area_m2 = (lat_span * m_per_deg_lat) * (lon_span * m_per_deg_lon)
    return area_m2 / 10000.0


def feature_to_single_geometry(feature: dict) -> dict:
    """Return a GeoJSON geometry for one feature (for per-village request)."""
    geom = feature.get("geometry")
    if not geom:
        return None
    return geom


# ---------------------------------------------------------------------------
# Sentinel Hub requests
# ---------------------------------------------------------------------------

# Credentials — Copernicus Data Space Ecosystem (CDSE), CLIENT_CREDENTIALS flow.
# Do not hardcode secrets; set SH_CLIENT_ID / SH_CLIENT_SECRET in your local .env (gitignored).
_DEFAULT_SH_CLIENT_ID = ""
_DEFAULT_SH_CLIENT_SECRET = ""

# CDSE endpoints (required for CDSE OAuth clients; legacy Sentinel Hub uses different URLs)
CDSE_BASE_URL = "https://sh.dataspace.copernicus.eu"
CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"


def get_config():
    """Build SHConfig for Copernicus Data Space Ecosystem (CDSE)."""
    config = SHConfig()
    config.sh_client_id = os.environ.get("SH_CLIENT_ID") or _DEFAULT_SH_CLIENT_ID
    config.sh_client_secret = os.environ.get("SH_CLIENT_SECRET") or _DEFAULT_SH_CLIENT_SECRET
    if not config.sh_client_id or not config.sh_client_secret:
        raise RuntimeError(
            "Missing SH_CLIENT_ID/SH_CLIENT_SECRET. Set them in your local .env (gitignored) before running."
        )
    config.sh_base_url = CDSE_BASE_URL
    config.sh_token_url = CDSE_TOKEN_URL
    if os.environ.get("SH_INSTANCE_ID"):
        config.instance_id = os.environ["SH_INSTANCE_ID"]
    return config


def _cdse_input_data(time_interval: tuple, maxcc: float):
    """Build input_data as plain dicts so request uses config.sh_base_url (CDSE). SENTINEL2_L2A.api_id forces MAIN URL otherwise."""
    start_time, end_time = serialize_time(
        parse_time_interval(time_interval, allow_undefined=True), use_tz=True
    )
    maxcc_pct = int(maxcc) if maxcc > 1 else int(maxcc * 100)  # API expects 0-100 integer
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


def _geometry_request_size(geometry_json: dict, resolution: tuple, min_pixels: int = 64):
    """Compute (width, height) for request so CDSE 1500 m/px limit is not exceeded (use min_pixels)."""
    bbox = _bbox_from_geometry(geometry_json)
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return (min_pixels, min_pixels)
    try:
        sh_bbox = BBox(bbox, crs=CRS.WGS84)
        w, h = bbox_to_dimensions(sh_bbox, resolution=resolution[0])
        w = max(int(w), min_pixels)
        h = max(int(h), min_pixels)
        return (w, h)
    except Exception:
        return (min_pixels, min_pixels)


def request_metric(config: SHConfig, geometry_json: dict, time_interval: tuple,
                   evalscript: str, resolution=(10, 10), maxcc=20):
    """Single Process API request; returns image array (H, W, C) or (H, W)."""
    geom = Geometry(geometry_json, crs=CRS("EPSG:4326"))
    size = _geometry_request_size(geometry_json, resolution)
    request = SentinelHubRequest(
        evalscript=evalscript,
        input_data=_cdse_input_data(time_interval, maxcc),
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        geometry=geom,
        size=size,
        config=config,
    )
    data = request.get_data()
    if not data:
        return None
    arr = np.array(data[0])
    return arr


def compute_stats_from_ndvi_cloud(arr: np.ndarray) -> tuple:
    """From 2-band output [NDVI, cloud], return (mean_ndvi, cloud_free_pct)."""
    if arr is None or arr.size == 0:
        return float("nan"), 0.0
    if arr.ndim == 2:
        ndvi = arr
        cloud = np.zeros_like(ndvi)
    else:
        ndvi = arr[:, :, 0]
        cloud = arr[:, :, 1]
    valid = ~np.isnan(ndvi) & (ndvi >= -1) & (ndvi <= 1)
    cloud_valid = np.isfinite(cloud)
    total = np.sum(cloud_valid)
    if total == 0:
        return float("nan"), 0.0
    cloud_free_pct = 100.0 * (1.0 - np.nanmean(cloud))
    ndvi_valid = valid & (cloud < 0.5)
    if np.any(ndvi_valid):
        mean_ndvi = float(np.nanmean(ndvi[ndvi_valid]))
    else:
        mean_ndvi = float(np.nanmean(ndvi[valid])) if np.any(valid) else float("nan")
    return mean_ndvi, max(0.0, min(100.0, cloud_free_pct))


def mean_of_band(arr: np.ndarray, band: int = 0) -> float:
    """Mean of one band over valid finite pixels in [-1, 1]. Returns nan if empty."""
    if arr is None or arr.size == 0:
        return float("nan")
    if arr.ndim == 2:
        b = arr
    else:
        b = arr[:, :, band]
    valid = np.isfinite(b) & (b >= -1) & (b <= 1)
    if not np.any(valid):
        return float("nan")
    return float(np.nanmean(b[valid]))


# ---------------------------------------------------------------------------
# Weekly time-series
# ---------------------------------------------------------------------------

def weekly_intervals(year: int):
    """Yield (start_date, end_date) for each week in year (ISO)."""
    from datetime import timedelta
    start = datetime(year, 1, 1)
    end = datetime(year, 12, 31)
    current = start
    while current <= end:
        week_end = current + timedelta(days=6)
        if week_end > end:
            week_end = end
        yield (current.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d"))
        current = current + timedelta(days=7)


# ---------------------------------------------------------------------------
# Export QGIS-ready
# ---------------------------------------------------------------------------

def _bbox_from_geometry(geom: dict) -> tuple:
    """Return (min_lon, min_lat, max_lon, max_lat) from GeoJSON geometry or FeatureCollection."""
    if geom.get("type") == "FeatureCollection":
        features = geom.get("features", [])
        if not features:
            return (0, 0, 0, 0)
        all_bounds = [_bbox_from_geometry(f.get("geometry") or {}) for f in features]
        min_lon = min(b[0] for b in all_bounds)
        min_lat = min(b[1] for b in all_bounds)
        max_lon = max(b[2] for b in all_bounds)
        max_lat = max(b[3] for b in all_bounds)
        return (min_lon, min_lat, max_lon, max_lat)
    coords = geom.get("coordinates")
    if not coords:
        return (0, 0, 0, 0)
    if geom.get("type") == "Polygon":
        ring = coords[0]
    elif geom.get("type") == "MultiPolygon":
        ring = []
        for poly in coords:
            ring.extend(poly[0])
    else:
        return (0, 0, 0, 0)
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return (min(lons), min(lats), max(lons), max(lats))


def request_geotiff_bytes(config: SHConfig, geometry_json: dict, time_interval: tuple,
                          evalscript: str, resolution=(10, 10), maxcc=20):
    """Request and return raw TIFF bytes or numpy array for saving to disk."""
    geom = Geometry(geometry_json, crs=CRS("EPSG:4326"))
    size = _geometry_request_size(geometry_json, resolution)
    request = SentinelHubRequest(
        evalscript=evalscript,
        input_data=_cdse_input_data(time_interval, maxcc),
        responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
        geometry=geom,
        size=size,
        config=config,
    )
    data = request.get_data()
    if not data:
        return None
    return data[0]


def save_geotiff(data, out_path: Path, geometry_json: dict = None) -> bool:
    """Save GeoTIFF from bytes or numpy array. If array and rasterio available, write with georeference."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        with open(out_path, "wb") as f:
            f.write(data)
        return True
    if isinstance(data, np.ndarray):
        try:
            import rasterio
            from rasterio.transform import from_bounds
            from rasterio.crs import CRS as RasterioCRS
            bbox = _bbox_from_geometry(geometry_json) if geometry_json else (0, 0, 1, 1)
            h, w = data.shape[0], data.shape[1]
            transform = from_bounds(bbox[0], bbox[1], bbox[2], bbox[3], w, h)
            bands = data.shape[2] if data.ndim == 3 else 1
            with rasterio.open(
                out_path, "w", driver="GTiff", width=w, height=h, count=bands,
                dtype=data.dtype, crs=RasterioCRS.from_epsg(4326), transform=transform,
            ) as dst:
                if data.ndim == 3:
                    for i in range(bands):
                        dst.write(data[:, :, i], i + 1)
                else:
                    dst.write(data, 1)
            return True
        except ImportError:
            # No rasterio: write raw array as npy for later use
            np.save(out_path.with_suffix(".npy"), data)
            logger.warning("rasterio not installed; saved %s (use for stats only)", out_path.with_suffix(".npy"))
            return False
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Batch process villages with Sentinel Hub: NDVI, NDWI, stats, weekly mosaics, QGIS export",
    )
    parser.add_argument(
        "--geojson",
        type=str,
        default="data/villages_medchal_malkajgiri.geojson",
        help="Path to village GeoJSON (FeatureCollection)",
    )
    parser.add_argument(
        "--district",
        type=str,
        default="Medchal-Malkajgiri",
        help="District name to filter (default: Medchal-Malkajgiri)",
    )
    parser.add_argument(
        "--max-villages",
        type=int,
        default=10,
        help="Max number of villages to process (default: 10)",
    )
    parser.add_argument(
        "--time-start",
        type=str,
        default="2026-02-01",
        help="Start date for main batch (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--time-end",
        type=str,
        default="2026-02-25",
        help="End date for main batch (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=2026,
        help="Year for weekly time-series mosaics",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/sentinel_output",
        help="Output directory for GeoTIFFs and GeoJSON",
    )
    parser.add_argument(
        "--resolution",
        type=int,
        default=10,
        help="Resolution in meters (default: 10)",
    )
    parser.add_argument(
        "--maxcc",
        type=int,
        default=20,
        help="Max cloud cover percent (default: 20)",
    )
    parser.add_argument(
        "--skip-weekly",
        action="store_true",
        help="Skip weekly time-series generation",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load GeoJSON and list villages only; no API calls",
    )
    parser.add_argument(
        "--layers",
        type=str,
        default="all",
        help="Comma-separated layer ids to extract (default: all). Available: " + ", ".join(L["id"] for L in LAYERS),
    )
    args = parser.parse_args()

    # Load and filter villages
    try:
        fc = load_village_geojson(args.geojson, district_filter=args.district, max_features=args.max_villages)
    except Exception as e:
        logger.error("Failed to load GeoJSON: %s", e)
        return 1
    features = fc["features"]
    if not features:
        logger.error("No features found for district=%s in %s", args.district, args.geojson)
        return 1
    logger.info("Loaded %d villages: %s", len(features),
                [f["properties"].get("village", "?") for f in features])

    if args.dry_run:
        return 0

    config = get_config()
    if not getattr(config, "sh_client_id", None) or not getattr(config, "sh_client_secret", None):
        logger.warning("SH_CLIENT_ID / SH_CLIENT_SECRET not set; requests may fail.")

    time_range = (args.time_start, args.time_end)
    resolution = (args.resolution, args.resolution)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Which layers to extract
    if args.layers.strip().lower() == "all":
        layers_to_run = list(LAYERS)
    else:
        ids = [s.strip().lower() for s in args.layers.split(",") if s.strip()]
        layers_to_run = [L for L in LAYERS if L["id"].lower() in ids]
        if not layers_to_run:
            logger.warning("No matching layers; using all. Available: %s", [L["id"] for L in LAYERS])
            layers_to_run = list(LAYERS)
    logger.info("Extracting layers: %s", [L["id"] for L in layers_to_run])

    # Per-village: request each layer, compute stats, export raster
    village_stats = []
    for i, feat in enumerate(features):
        village_name = (feat.get("properties") or {}).get("village", f"village_{i}")
        geom = feature_to_single_geometry(feat)
        if not geom:
            logger.warning("Skip %s: no geometry", village_name)
            continue
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in village_name).strip().replace(" ", "_")

        area_ha = polygon_area_ha(geom)
        st = {
            "village": village_name,
            "district": (feat.get("properties") or {}).get("district", ""),
            "state": (feat.get("properties") or {}).get("state", ""),
            "cloud_free_pct": None,
            "area_ha": round(area_ha, 2),
        }

        for layer in layers_to_run:
            lid = layer["id"]
            arr = request_metric(
                config, geom, time_range, layer["evalscript"], resolution=resolution, maxcc=args.maxcc
            )
            if arr is None:
                continue
            # Stats from this layer
            if layer.get("stat") == "ndvi_cloud":
                mean_val, cloud_pct = compute_stats_from_ndvi_cloud(arr)
                st["mean_" + lid] = round(mean_val, 4) if not np.isnan(mean_val) else None
                st["cloud_free_pct"] = round(cloud_pct, 2)
            elif "stat_band" in layer:
                mean_val = mean_of_band(arr, layer["stat_band"])
                st["mean_" + lid] = round(mean_val, 4) if not np.isnan(mean_val) else None
            # Export raster (reuse same array to avoid duplicate API call)
            path = out_dir / f"{safe_name}_{lid}.tif"
            if save_geotiff(arr, path, geom):
                logger.info("Wrote %s", path)

        if st.get("cloud_free_pct") is None:
            st["cloud_free_pct"] = 0.0
        village_stats.append(st)
        means = [f"{k}={st[k]:.4f}" for k in sorted(st) if k.startswith("mean_") and st[k] is not None]
        logger.info("Village %s: %s cloud_free=%.1f%% area_ha=%.2f",
                    village_name, " ".join(means) if means else "(no indices)", st["cloud_free_pct"], st["area_ha"])

    # Summary statistics table
    stats_path = out_dir / "village_statistics.json"
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(village_stats, f, indent=2)
    logger.info("Wrote %s", stats_path)

    # QGIS-ready GeoJSON: boundaries with all layer stats as attributes
    qgis_fc = {
        "type": "FeatureCollection",
        "features": [],
    }
    for feat, st in zip(features, village_stats):
        props = {k: v for k, v in st.items()}
        qgis_fc["features"].append({
            "type": "Feature",
            "properties": props,
            "geometry": feat.get("geometry"),
        })
    qgis_path = out_dir / "villages_qgis.geojson"
    with open(qgis_path, "w", encoding="utf-8") as f:
        json.dump(qgis_fc, f, indent=2, ensure_ascii=False)
    logger.info("Wrote QGIS layer %s", qgis_path)

    # Weekly time-series mosaics (one composite per week for full AOI)
    if not args.skip_weekly:
        combined_geom = fc  # full FeatureCollection as AOI
        for week_start, week_end in weekly_intervals(args.year):
            week_label = f"{args.year}_w{datetime.strptime(week_start, '%Y-%m-%d').strftime('%V')}"
            logger.info("Weekly mosaic %s (%s to %s)", week_label, week_start, week_end)
            tiff_data = request_geotiff_bytes(
                config, combined_geom, (week_start, week_end),
                EVALSCRIPT_NDVI_CLOUD, resolution=resolution, maxcc=args.maxcc
            )
            if tiff_data is not None:
                path = out_dir / f"mosaic_ndvi_{week_label}.tif"
                if save_geotiff(tiff_data, path, combined_geom):
                    logger.info("Wrote %s", path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
