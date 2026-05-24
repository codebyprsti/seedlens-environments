"""AOI geometry helpers for field validation (no Sentinel pipeline dependency)."""

from __future__ import annotations

from typing import Any


def geojson_polygon_area_ha(geojson: dict[str, Any]) -> float:
    """Polygon area in hectares (WGS84 → Web Mercator), same approach as crop_monitoring.pipeline."""
    try:
        from shapely.geometry import shape
        from shapely.ops import transform
        import pyproj

        geom = shape(geojson)
        proj = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        geom_m = transform(proj.transform, geom)
        return float(geom_m.area / 10_000.0)
    except Exception:
        return float("nan")
