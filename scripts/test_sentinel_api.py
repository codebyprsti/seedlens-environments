#!/usr/bin/env python3
"""
Standalone test for Sentinel layer API flow (no FastAPI app required).
Tests: fetch_raw_bands -> compute_layer_from_bands -> GeoTIFF bytes.
Run from project root with SH_CLIENT_ID and SH_CLIENT_SECRET set (or defaults in sentinel_service).
"""
import os
import sys
from pathlib import Path

# Project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# NOTE: Do not hardcode credentials. Set SH_CLIENT_ID and SH_CLIENT_SECRET in your local .env.

def main():
    from services.sentinel_service import fetch_raw_bands
    from services.index_calculator import compute_layer_from_bands

    # Small bbox near Hyderabad (same as village batch)
    bbox = (78.48, 17.51, 78.50, 17.53)
    time_interval = ("2026-02-01", "2026-02-25")

    print("1. Fetching raw bands (single Sentinel API call)...")
    bands, bbox_out, width, height = fetch_raw_bands(
        bbox, time_interval, maxcc=20, resolution=10, use_cache=True
    )
    print(f"   Got shape {bands.shape}, bbox={bbox_out}, size={width}x{height}")

    for layer_name in ["ndvi", "ndwi", "true_color", "moisture_index", "scl"]:
        print(f"2. Computing layer: {layer_name}")
        layer = compute_layer_from_bands(bands, layer_name)
        if layer.ndim == 2:
            valid = ~__import__("numpy").isnan(layer)
            mean_val = layer[valid].mean() if valid.any() else float("nan")
            print(f"   Shape {layer.shape}, mean={mean_val:.4f}")
        else:
            print(f"   Shape {layer.shape} (RGB/composite)")

    # GeoTIFF export (requires rasterio)
    print("3. Encoding NDVI to GeoTIFF bytes...")
    try:
        import numpy as np
        import io
        import rasterio
        from rasterio.crs import CRS as RasterioCRS
        from rasterio.transform import from_bounds
        min_lon, min_lat, max_lon, max_lat = bbox_out
        layer = compute_layer_from_bands(bands, "ndvi")
        data = np.asarray(layer, dtype=np.float32)
        transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)
        buf = io.BytesIO()
        with rasterio.open(
            buf, "w", driver="GTiff", width=width, height=height, count=1,
            dtype=np.float32, crs=RasterioCRS.from_epsg(4326), transform=transform, nodata=float("nan"),
        ) as dst:
            dst.write(data, 1)
        buf.seek(0)
        tiff_bytes = buf.read()
        out_path = Path(__file__).parent.parent / "demo_outputs" / "sentinel_api_test_ndvi.tif"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(tiff_bytes)
        print(f"   Wrote {len(tiff_bytes)} bytes -> {out_path}")
    except ImportError as e:
        print(f"   Skip GeoTIFF write (rasterio not installed): {e}")

    print("Done. Sentinel API flow is working.")

if __name__ == "__main__":
    main()
