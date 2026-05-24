"""
Sentinel layer API: single Sentinel call per request, compute requested layer in backend, return GeoTIFF.
Village-summary endpoint returns CSV of layer statistics (ndvi, ndwi, moisture_index, true_color, scl).
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Optional

import numpy as np
from fastapi import APIRouter, Query
from fastapi.responses import Response, StreamingResponse

from services.index_calculator import compute_layer_from_bands
from services.layer_statistics import compute_layer_statistics
from services.sentinel_service import fetch_raw_bands

logger = logging.getLogger(__name__)

router = APIRouter()

# CSV header for village-summary
VILLAGE_SUMMARY_CSV_HEADER = [
    "village_name", "district", "state",
    "min_lon", "min_lat", "max_lon", "max_lat",
    "date_from", "date_to",
    "layer_name", "mean", "min", "max", "std_dev", "valid_pixel_count",
    "bands", "height", "width", "status",
]
VILLAGE_SUMMARY_LAYERS = ["ndvi", "ndwi", "moisture_index", "true_color", "scl"]

# Layer names accepted by GET /sentinel/layer
LAYER_NAMES = [
    "true_color",
    "ndvi",
    "ndwi",
    "ndsi",
    "moisture_index",
    "swir",
    "scl",
]


def _layer_array_to_geotiff_bytes(
    layer_array,  # np.ndarray (H,W) or (H,W,3)
    bbox: tuple,
    width: int,
    height: int,
) -> bytes:
    """Encode (H,W) or (H,W,3) float array to GeoTIFF bytes, CRS EPSG:4326."""
    try:
        import rasterio
        from rasterio.crs import CRS as RasterioCRS
        from rasterio.transform import from_bounds
    except ImportError:
        raise RuntimeError("rasterio is required to return GeoTIFF; pip install rasterio")
    min_lon, min_lat, max_lon, max_lat = bbox
    if layer_array.ndim == 2:
        count = 1
        data = np.asarray(layer_array, dtype=np.float32)
    else:
        count = layer_array.shape[2]
        data = np.asarray(layer_array, dtype=np.float32)
    transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)
    buf = io.BytesIO()
    with rasterio.open(
        buf,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=count,
        dtype=np.float32,
        crs=RasterioCRS.from_epsg(4326),
        transform=transform,
        nodata=float("nan"),
    ) as dst:
        if count == 1:
            dst.write(data, 1)
        else:
            for i in range(count):
                dst.write(data[:, :, i], i + 1)
    buf.seek(0)
    return buf.read()


@router.get(
    "/layer",
    response_class=Response,
    responses={
        200: {
            "content": {"image/tiff": {}},
            "description": "GeoTIFF of the requested layer",
        },
    },
)
def get_sentinel_layer(
    layer_name: str = Query(..., description="Layer to return: true_color, ndvi, ndwi, ndsi, moisture_index, swir, scl"),
    min_lon: float = Query(..., description="Bbox min longitude (WGS84)"),
    min_lat: float = Query(..., description="Bbox min latitude (WGS84)"),
    max_lon: float = Query(..., description="Bbox max longitude (WGS84)"),
    max_lat: float = Query(..., description="Bbox max latitude (WGS84)"),
    date_from: str = Query(..., description="Start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="End date YYYY-MM-DD (default: same as date_from)"),
    maxcc: int = Query(20, ge=0, le=100, description="Max cloud coverage %"),
    use_cache: bool = Query(True, description="Use in-memory cache for raw bands"),
):
    """
    Return a single Sentinel-2 derived layer as GeoTIFF.

    Sentinel is called once per request to fetch raw bands (B02,B03,B04,B08,B8A,B11,B12,SCL)
    at 10m resolution. The requested layer is computed in the backend and returned as GeoTIFF.
    Raw band response is cached for the same bbox/date to avoid repeated API calls.
    """
    if not layer_name.strip():
        return Response(content=b"layer_name required", status_code=400)
    if (layer_name.strip().lower() not in LAYER_NAMES
            and layer_name.strip().lower() not in ("ndmi", "moisture", "rgb", "true color", "snow")):
        return Response(
            content=f"Unknown layer_name. Supported: {', '.join(LAYER_NAMES)}".encode(),
            status_code=400,
        )
    bbox = (min_lon, min_lat, max_lon, max_lat)
    if min_lon >= max_lon or min_lat >= max_lat:
        return Response(content=b"Invalid bbox: min must be < max", status_code=400)
    time_interval = (date_from, date_to or date_from)
    try:
        bands, bbox_out, width, height = fetch_raw_bands(
            bbox,
            time_interval,
            maxcc=maxcc,
            resolution=10,
            use_cache=use_cache,
        )
    except Exception as e:
        logger.exception("Sentinel fetch_raw_bands failed")
        return Response(content=str(e).encode(), status_code=502)
    try:
        layer_array = compute_layer_from_bands(bands, layer_name)
    except ValueError as e:
        return Response(content=str(e).encode(), status_code=400)
    try:
        tiff_bytes = _layer_array_to_geotiff_bytes(layer_array, bbox_out, width, height)
    except RuntimeError as e:
        logger.warning("%s", e)
        return Response(content=str(e).encode(), status_code=501)
    return Response(content=tiff_bytes, media_type="image/tiff")


def _village_summary_csv_rows(
    village_name: str,
    district: str,
    state: str,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    date_from: str,
    date_to: str,
    height: int,
    width: int,
    status: str,
    bands_arr: np.ndarray,
) -> list[list[str]]:
    """Build CSV rows for village-summary: one row per layer (ndvi, ndwi, moisture_index, true_color, scl)."""
    rows = []
    base = [
        village_name, district, state,
        str(min_lon), str(min_lat), str(max_lon), str(max_lat),
        date_from, date_to,
    ]
    for layer_name in VILLAGE_SUMMARY_LAYERS:
        try:
            layer_array = compute_layer_from_bands(bands_arr, layer_name)
        except ValueError:
            rows.append(base + [layer_name, "", "", "", "", "", "0", str(height), str(width), "error"])
            continue
        if layer_array.ndim == 3:
            # true_color: bands=3, no mean/min/max/std_dev/valid_pixel_count
            row = base + [
                layer_name, "", "", "", "", "",
                "3", str(height), str(width), status,
            ]
        else:
            stats = compute_layer_statistics(layer_array)
            mean_s = f"{stats['mean']:.6f}" if stats["mean"] is not None else ""
            min_s = f"{stats['min']:.6f}" if stats["min"] is not None else ""
            max_s = f"{stats['max']:.6f}" if stats["max"] is not None else ""
            std_s = f"{stats['std_dev']:.6f}" if stats["std_dev"] is not None else ""
            n_s = str(stats["valid_pixel_count"]) if stats["valid_pixel_count"] is not None else "0"
            row = base + [
                layer_name, mean_s, min_s, max_s, std_s, n_s,
                "1", str(height), str(width), status,
            ]
        rows.append(row)
    return rows


def _stream_csv(rows: list[list[str]], header: list[str]) -> bytes:
    """Return CSV as bytes (in-memory), suitable for StreamingResponse body."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


@router.get(
    "/village-summary",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/csv": {}},
            "description": "CSV of layer statistics per village (ndvi, ndwi, moisture_index, true_color, scl)",
        },
        400: {"description": "Invalid parameters (e.g. invalid bbox)"},
        502: {"description": "Sentinel API error"},
    },
)
def get_sentinel_village_summary(
    village_name: str = Query(..., description="Village name"),
    district: str = Query(..., description="District"),
    state: str = Query(..., description="State"),
    min_lon: float = Query(..., description="Bbox min longitude (WGS84)"),
    min_lat: float = Query(..., description="Bbox min latitude (WGS84)"),
    max_lon: float = Query(..., description="Bbox max longitude (WGS84)"),
    max_lat: float = Query(..., description="Bbox max latitude (WGS84)"),
    date_from: str = Query(..., description="Start date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="End date YYYY-MM-DD (default: same as date_from)"),
    max_cloud_cover: Optional[int] = Query(20, ge=0, le=100, description="Max cloud coverage %"),
    use_cache: bool = Query(True, description="Use in-memory cache for raw bands"),
):
    """
    Return a CSV summary of layer statistics for the given village and bbox.

    Calls fetch_raw_bands() once, then computes ndvi, ndwi, moisture_index, true_color, scl.
    For each numeric layer: mean, min, max, std_dev, valid_pixel_count.
    For true_color: bands=3 only (no mean/min/max). Output is CSV (StreamingResponse).

    Example curl:
      curl -o village_summary.csv "http://localhost:8020/sentinel/village-summary?village_name=Kompally&district=Medchal&state=Telangana&min_lon=78.48&min_lat=17.51&max_lon=78.50&max_lat=17.53&date_from=2026-02-01&date_to=2026-02-25&max_cloud_cover=20"

    Example CSV output (first rows):
      village_name,district,state,min_lon,min_lat,max_lon,max_lat,date_from,date_to,layer_name,mean,min,max,std_dev,valid_pixel_count,bands,height,width,status
      Kompally,Medchal,Telangana,78.48,17.51,78.5,17.53,2026-02-01,2026-02-25,ndvi,0.227700,-0.523100,0.891200,0.234500,42000,1,219,215,ok
      Kompally,Medchal,Telangana,78.48,17.51,78.5,17.53,2026-02-01,2026-02-25,ndwi,-0.314900,-0.812000,0.445000,0.156200,42000,1,219,215,ok
      ...
      Kompally,Medchal,Telangana,78.48,17.51,78.5,17.53,2026-02-01,2026-02-25,true_color,,,,,,3,219,215,ok
    """
    bbox = (min_lon, min_lat, max_lon, max_lat)
    if min_lon >= max_lon or min_lat >= max_lat:
        return Response(content=b"Invalid bbox: min must be < max", status_code=400)
    time_interval = (date_from, date_to or date_from)
    maxcc = max_cloud_cover if max_cloud_cover is not None else 20
    try:
        bands, bbox_out, width, height = fetch_raw_bands(
            bbox,
            time_interval,
            maxcc=maxcc,
            resolution=10,
            use_cache=use_cache,
        )
    except Exception as e:
        logger.exception("Sentinel fetch_raw_bands failed in village-summary")
        return Response(content=str(e).encode(), status_code=502)
    min_lon_out, min_lat_out, max_lon_out, max_lat_out = bbox_out
    rows = _village_summary_csv_rows(
        village_name=village_name,
        district=district,
        state=state,
        min_lon=min_lon_out,
        min_lat=min_lat_out,
        max_lon=max_lon_out,
        max_lat=max_lat_out,
        date_from=date_from,
        date_to=date_to or date_from,
        height=height,
        width=width,
        status="ok",
        bands_arr=bands,
    )
    csv_bytes = _stream_csv(rows, VILLAGE_SUMMARY_CSV_HEADER)
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=village_summary.csv"},
    )
