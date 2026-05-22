"""
Parse KML files: extract polygon coordinates (lon, lat; altitude ignored) and convert to GeoJSON.
Validates geometry with Shapely.

Polygon-first: outerBoundaryIs / LinearRing coordinates are used before any <Point>.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Extract area_acre from description: "1.56acre", "Area_Acre: 2.34", "Area Acre: 2.34"
AREA_ACRE_PATTERNS = [
    re.compile(r"area[_ ]?acre[: ]*([0-9.]+)", re.IGNORECASE),
    re.compile(r"\b(\d+\.?\d*)\s*acre\b", re.IGNORECASE),
]
# Extract distance_km from description: "0.35km", "Distance_KM: 0.56", "Distance KM: 0.56"
DISTANCE_KM_PATTERNS = [
    re.compile(r"distance[_ ]?km[: ]*([0-9.]+)", re.IGNORECASE),
    re.compile(r"\b(\d+\.?\d*)\s*km\b", re.IGNORECASE),
]

try:
    from fastkml import kml
except ImportError:
    kml = None

try:
    from shapely.geometry import Polygon
    from shapely.geometry import shape as shapely_shape
    from shapely.validation import make_valid
except ImportError:
    Polygon = None
    shapely_shape = None
    make_valid = None


def _force_2d(geom):
    """Drop Z / M dimensions so coordinates are lon/lat only (KML altitude ignored)."""
    if geom is None:
        return None
    try:
        from shapely import force_2d

        return force_2d(geom)
    except (ImportError, AttributeError):
        from shapely.ops import transform

        def _xy(x, y, z=None, *args):
            return (x, y)

        return transform(_xy, geom)


def _extract_largest_polygon(geom):
    """
    Recursively extract Polygon instances from Polygon, MultiPolygon, GeometryCollection,
    or nested collections. Ignores Point, LineString, etc.

    Returns the single largest polygon by area, or None if none found.
    """
    from shapely.geometry import GeometryCollection, MultiPolygon, Polygon as SPolygon

    if geom is None or geom.is_empty:
        return None

    gtype = geom.geom_type

    if gtype == "Polygon":
        return geom

    if gtype == "LinearRing":
        try:
            return SPolygon(geom)
        except Exception:
            return None

    if gtype == "MultiPolygon":
        polys = [g for g in geom.geoms if g is not None and not g.is_empty and g.geom_type == "Polygon"]
        if not polys:
            return None
        return max(polys, key=lambda g: g.area)

    if gtype == "GeometryCollection":
        candidates = []
        for g in geom.geoms:
            p = _extract_largest_polygon(g)
            if p is not None and not p.is_empty:
                candidates.append(p)
        if not candidates:
            return None
        return max(candidates, key=lambda g: g.area)

    return None


def _geometry_to_polygon_geojson(geom) -> dict:
    """
    Convert any Shapely geometry to a valid GeoJSON Polygon dict.
    Handles Polygon, MultiPolygon (largest part), GeometryCollection (recursive polygon harvest),
    and nested structures from make_valid / fastkml. Z coordinates are stripped.
    Output is always a Polygon (not MultiPolygon) for downstream APIs that reject collections.
    """
    if geom is None:
        raise ValueError("Geometry is None")

    geom = _force_2d(geom)
    poly = _extract_largest_polygon(geom)

    if poly is None:
        raise ValueError(
            "No polygon geometry found (GeometryCollection had no Polygon parts, or unsupported type)"
        )

    if not poly.is_valid and make_valid is not None:
        fixed = make_valid(poly)
        fixed = _force_2d(fixed)
        poly = _extract_largest_polygon(fixed)
        if poly is None:
            raise ValueError("make_valid produced geometry with no extractable polygon")

    return poly.__geo_interface__


def _parse_coordinates_text(text: str) -> list[tuple[float, float]]:
    """Parse KML coordinates string (lon,lat,alt or lon,lat). Altitude ignored."""
    points: list[tuple[float, float]] = []
    for line in text.split():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) >= 2:
            lon, lat = float(parts[0]), float(parts[1])
            points.append((lon, lat))
    return points


def _local_name(tag: str) -> str:
    if not tag:
        return ""
    if "}" in tag:
        return tag.split("}", 1)[-1]
    return tag


def _xml_polygon_coordinates_first(root: Any, ns: str) -> list[tuple[float, float]]:
    """
    Prefer Polygon → outerBoundaryIs → LinearRing → coordinates.
    Ignores standalone <Point> until polygon scan completes.
    """
    coords: list[tuple[float, float]] = []
    for el in root.iter():
        if _local_name(el.tag) != "Polygon":
            continue
        outer = None
        for child in el:
            if _local_name(child.tag) == "outerBoundaryIs":
                outer = child
                break
        if outer is None:
            continue
        ring = None
        for child in outer:
            if _local_name(child.tag) == "LinearRing":
                ring = child
                break
        if ring is None:
            continue
        coords_el = None
        for child in ring:
            if _local_name(child.tag) == "coordinates":
                coords_el = child
                break
        if coords_el is None and ns:
            coords_el = ring.find(f"{ns}coordinates")
        if coords_el is not None and coords_el.text and coords_el.text.strip():
            pts = _parse_coordinates_text(coords_el.text.strip())
            if len(pts) >= 3:
                logger.info("Using Polygon coordinates")
                return pts
            coords.extend(pts)
    if coords and len(coords) >= 3:
        logger.info("Using Polygon coordinates")
        return coords
    return []


def _xml_point_coordinates_fallback(root: Any, ns: str) -> list[tuple[float, float]]:
    """Single-point geometry when no valid polygon exists (not a closed polygon; caller may error)."""
    for el in root.iter():
        if _local_name(el.tag) != "Point":
            continue
        coords_el = None
        for child in el:
            if _local_name(child.tag) == "coordinates":
                coords_el = child
                break
        if coords_el is None and ns:
            coords_el = el.find(f"{ns}coordinates")
        if coords_el is not None and coords_el.text and coords_el.text.strip():
            pts = _parse_coordinates_text(coords_el.text.strip())
            if pts:
                logger.warning("Using Point coordinates (no Polygon found)")
                return pts
    return []


def _coords_from_fastkml_geometry(g) -> list[tuple[float, float]]:
    """Extract exterior ring coords from a shapely-like or fastkml geometry; polygons only."""
    coords: list[tuple[float, float]] = []
    if g is None:
        return coords
    gtype = getattr(g, "geom_type", None) or type(g).__name__
    if gtype == "Point":
        return coords
    if hasattr(g, "geoms"):
        for sub in g.geoms:
            c = _coords_from_fastkml_geometry(sub)
            if len(c) >= 3:
                return c
        return coords
    if hasattr(g, "exterior") and g.exterior is not None:
        ring = g.exterior
        if hasattr(ring, "coords"):
            for c in ring.coords:
                coords.append((float(c[0]), float(c[1])))
        else:
            for c in ring.__geo_interface__.get("coordinates", []):
                coords.append((float(c[0]), float(c[1])))
    return coords


def parse_kml(kml_path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Read KML and return (geojson_geometry, metadata).

    - geojson_geometry: GeoJSON dict for the first polygon (type, coordinates).
    - metadata: dict with "placemark_name" (str) for metadata extraction.

    Coordinates use (lon, lat); altitude from KML is ignored.
    """
    path = Path(kml_path)
    if not path.exists():
        raise FileNotFoundError(f"KML file not found: {kml_path}")

    placemark_name = ""
    coords: list[tuple[float, float]] = []

    if kml is not None:
        try:
            with path.open("rb") as f:
                doc = kml.KML()
                doc.from_string(f.read())
            for feature in doc.features():
                if hasattr(feature, "features"):
                    for pm in feature.features():
                        if hasattr(pm, "name") and pm.name:
                            placemark_name = pm.name
                        if hasattr(pm, "geometry") and pm.geometry is not None:
                            g = pm.geometry
                            gtype = getattr(g, "geom_type", None) or type(g).__name__
                            if gtype == "Point":
                                continue
                            cand = _coords_from_fastkml_geometry(g)
                            if len(cand) >= 3:
                                coords = cand
                                logger.info("Using Polygon coordinates")
                                break
                        if coords:
                            break
                    if coords:
                        break
        except Exception:
            pass

    if not coords:
        import xml.etree.ElementTree as ET
        ns = "{http://www.opengis.net/kml/2.2}"
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            # Fallback: read with encoding fix for invalid chars (e.g. in CDATA/description)
            with path.open(encoding="utf-8", errors="replace") as f:
                content = f.read()
            root = ET.fromstring(content)
            tree = None
        else:
            root = tree.getroot()
        name_el = root.find(f".//{ns}name")
        if name_el is not None and name_el.text:
            placemark_name = name_el.text.strip()
        coords = _xml_polygon_coordinates_first(root, ns)
        if len(coords) < 3:
            coords = _xml_point_coordinates_fallback(root, ns)
        if len(coords) < 3:
            coords_el = root.find(f".//{ns}coordinates")
            if coords_el is None:
                for el in root.iter():
                    if _local_name(el.tag) == "coordinates":
                        coords_el = el
                        break
            if coords_el is not None and coords_el.text:
                coords = _parse_coordinates_text(coords_el.text.strip())

    # Point-only placemark: expand to a minimal triangle for downstream polygon APIs
    if len(coords) == 1:
        lon, lat = coords[0]
        eps = 1e-5
        coords = [(lon, lat), (lon + eps, lat), (lon, lat + eps), (lon, lat)]
        logger.warning("Using Point coordinates (no Polygon); expanded to micro-polygon for compatibility")

    if len(coords) < 3:
        raise ValueError("KML must contain a polygon with at least 3 points (or a single Point)")

    if coords[0] != coords[-1]:
        coords.append(coords[0])

    if Polygon is not None:
        poly = Polygon(coords)
        if not poly.is_valid and make_valid is not None:
            poly = make_valid(poly)
        geojson = _geometry_to_polygon_geojson(poly)
    else:
        geojson = {"type": "Polygon", "coordinates": [coords]}

    metadata = {"placemark_name": placemark_name, "area_acre": None, "distance_km": None, "village_name": None, "extracted_grower": None}
    # Extract village and grower from placemark name (multi-stage entity extraction)
    if placemark_name:
        try:
            from crop_monitoring.utils.entity_extractor import extract_village_and_grower
            extracted = extract_village_and_grower(placemark_name)
            if extracted.get("village_name"):
                metadata["village_name"] = extracted["village_name"]
            if extracted.get("grower_name"):
                metadata["extracted_grower"] = extracted["grower_name"]
        except Exception:
            pass
    # Parse description and ExtendedData from raw XML for area_acre, distance_km, village_name
    import xml.etree.ElementTree as ET
    ns = "{http://www.opengis.net/kml/2.2}"
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            content = f.read()
        root = ET.fromstring(content)
        # Extract area_acre and distance_km from Placemark description (e.g. "1.56acre", "0.35km")
        desc_el = root.find(f".//{ns}description")
        if desc_el is not None:
            desc_text = "".join(desc_el.itertext()) if hasattr(desc_el, "itertext") else (desc_el.text or "")
            desc_text = desc_text.replace("\n", " ").replace("\r", " ")
            if desc_text.strip():
                for pat in AREA_ACRE_PATTERNS:
                    m = pat.search(desc_text)
                    if m:
                        try:
                            metadata["area_acre"] = float(m.group(1).replace(",", ""))
                            break
                        except ValueError:
                            pass
                for pat in DISTANCE_KM_PATTERNS:
                    m = pat.search(desc_text)
                    if m:
                        try:
                            metadata["distance_km"] = float(m.group(1).replace(",", ""))
                            break
                        except ValueError:
                            pass
        for data_el in root.iter(ns + "Data"):
            name_attr = data_el.get("name")
            val_el = data_el.find(ns + "value")
            val = (val_el.text or "").strip() if val_el is not None else ""
            if not name_attr or not val:
                continue
            key = name_attr.strip().lower().replace(" ", "_")
            if key in ("area_acre", "area", "area_in_acre", "areaacre"):
                try:
                    metadata["area_acre"] = float(val.replace(",", ""))
                except ValueError:
                    pass
            elif key in ("distance_km", "distance", "distance_in_km", "distancekm"):
                try:
                    metadata["distance_km"] = float(val.replace(",", ""))
                except ValueError:
                    pass
            elif key in ("village_name", "village", "village_name_from_kml", "extracted_village"):
                metadata["village_name"] = val.strip()[:200]
            elif key in ("grower_name", "grower", "extracted_grower"):
                metadata["extracted_grower"] = val.strip()[:200]
    except Exception:
        pass
    if metadata["village_name"] is None and placemark_name:
        metadata["village_name"] = placemark_name.strip()[:200]
    return geojson, metadata


def get_polygon_geometry(kml_path: str | Path):
    """Return a Shapely Polygon for the first polygon in the KML. Validates geometry."""
    if Polygon is None:
        raise RuntimeError("shapely is required")
    geojson, _ = parse_kml(kml_path)
    from shapely.geometry import shape
    geom = shape(geojson)
    if not geom.is_valid and make_valid is not None:
        geom = make_valid(geom)
    return geom
