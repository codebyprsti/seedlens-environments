"""
OpenStreetMap Overpass API client for fetching administrative boundary polygons.

Queries relations with boundary=administrative by village + state (and optional district).
Converts Overpass JSON response to GeoJSON Polygon/MultiPolygon for storage.
"""

import logging
import requests
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Public Overpass API (504 timeouts possible; set OVERPASS_ENDPOINT env to override)
OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 45


def _escape_overpass_string(s: str) -> str:
    """Escape double quotes and backslashes for Overpass QL string."""
    if not s:
        return ""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_overpass_query(
    village: str,
    state: str,
    district: Optional[str] = None,
    use_area: bool = True,
) -> str:
    """
    Build Overpass QL query for administrative boundary.

    - If use_area: search area by state name, then relation(area) with name=village.
    - Else: search relation globally by name and optional is_in/addr tags.

    Returns query string.
    """
    village_esc = _escape_overpass_string((village or "").strip())
    state_esc = _escape_overpass_string((state or "").strip())
    if not village_esc or not state_esc:
        raise ValueError("village and state are required")

    if use_area:
        # Area filter: relation named state, then relations in that area named village
        query = (
            f'[out:json][timeout:{OVERPASS_TIMEOUT}];\n'
            f'area["name"="{state_esc}"]->.a;\n'
            f'(\n'
            f'  relation(area.a)["boundary"="administrative"]["name"="{village_esc}"];\n'
            f');\n'
            f'out body;\n'
            f'out geom;'
        )
    else:
        # Global search: relation by name; filter by state tag if present in OSM
        query = (
            f'[out:json][timeout:{OVERPASS_TIMEOUT}];\n'
            f'(\n'
            f'  relation["boundary"="administrative"]["name"="{village_esc}"]'
            f'["is_in:state"="{state_esc}"];\n'
            f'  relation["boundary"="administrative"]["name"="{village_esc}"]'
            f'["addr:state"="{state_esc}"];\n'
            f');\n'
            f'out body;\n'
            f'out geom;'
        )
    return query


def _get_endpoint() -> str:
    import os
    return os.environ.get("OVERPASS_ENDPOINT", OVERPASS_ENDPOINT)


def fetch_overpass(query: str) -> Dict[str, Any]:
    """
    POST query to Overpass API and return parsed JSON.
    Use env OVERPASS_ENDPOINT to override (e.g. https://overpass.kumi.systems/api/interpreter).

    Raises on HTTP error or invalid JSON. Returns {"elements": [...]}.
    """
    url = _get_endpoint()
    resp = requests.post(
        url,
        data={"data": query},
        timeout=OVERPASS_TIMEOUT,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


def _ring_from_member_geometry(member: Dict[str, Any]) -> List[List[float]]:
    """Convert Overpass member geometry ([{lat, lon}]) to GeoJSON ring [[lon, lat], ...] (closed)."""
    geom = member.get("geometry") or []
    if not geom:
        return []
    ring = [[float(p.get("lon", 0)), float(p.get("lat", 0))] for p in geom]
    if len(ring) < 3:
        return []
    if ring[0] != ring[-1]:
        ring.append(ring[0][:])
    return ring


def _point_in_polygon(point: List[float], ring: List[List[float]]) -> bool:
    """Ray-casting: true if point is inside polygon defined by ring (closed)."""
    x, y = point[0], point[1]
    n = len(ring)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _relation_to_geojson(relation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Convert one Overpass relation (with members having geometry) to GeoJSON Polygon or MultiPolygon.

    - Members with role "outer" -> polygons (or outer rings).
    - Members with role "inner" -> holes; assign to containing outer.
    - One outer (+ holes) -> one polygon. Multiple outers -> MultiPolygon.
    """
    members = relation.get("members") or []
    outers: List[List[List[float]]] = []
    inners: List[List[List[float]]] = []

    for m in members:
        if m.get("type") != "way":
            continue
        role = (m.get("role") or "").strip().lower()
        ring = _ring_from_member_geometry(m)
        if not ring:
            continue
        if role == "outer":
            outers.append(ring)
        elif role == "inner":
            inners.append(ring)

    if not outers:
        return None

    # Assign each inner to the outer that contains it (use first point of inner)
    polygons: List[List[List[List[float]]]] = []
    for outer in outers:
        holes: List[List[List[float]]] = []
        for inner in inners:
            if inner and _point_in_polygon(inner[0], outer):
                holes.append(inner)
        # GeoJSON Polygon: [exterior_ring, hole1, hole2, ...]
        polygons.append([outer] + holes)

    if len(polygons) == 0:
        return None
    if len(polygons) == 1 and len(polygons[0]) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def overpass_response_to_geojson(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Convert Overpass API response to a single GeoJSON geometry (Polygon or MultiPolygon).

    Uses the first relation that yields a valid geometry. If multiple relations,
    merges their polygons into one MultiPolygon (or returns first if only one).
    """
    elements = data.get("elements") or []
    geometries: List[Dict[str, Any]] = []

    for el in elements:
        if el.get("type") != "relation":
            continue
        geom = _relation_to_geojson(el)
        if geom:
            geometries.append(geom)

    if not geometries:
        return None
    if len(geometries) == 1:
        return geometries[0]
    # Multiple relations: merge into MultiPolygon
    all_polygons: List[List[List[List[float]]]] = []
    for g in geometries:
        if g.get("type") == "Polygon":
            all_polygons.append(g["coordinates"])
        elif g.get("type") == "MultiPolygon":
            all_polygons.extend(g["coordinates"])
    return {"type": "MultiPolygon", "coordinates": all_polygons}


def fetch_village_boundary(
    village: str,
    state: str,
    district: Optional[str] = None,
    use_area: bool = True,
) -> Dict[str, Any]:
    """
    Fetch village boundary from Overpass and return as GeoJSON-ready result.
    Tries area-based query first; if no results, retries with global (is_in:state) query.

    Returns:
        {"success": True, "geometry": {...}} or
        {"success": False, "error": "...", "api_404": bool, "api_empty_response": bool}
    """
    try:
        query = build_overpass_query(village, state, district, use_area=use_area)
        logger.debug("[Overpass] query (first 200 chars): %s", query[:200])
        data = fetch_overpass(query)
        geometry = overpass_response_to_geojson(data)
        if not geometry and use_area:
            # Fallback: global search by name + state tag
            query_global = build_overpass_query(village, state, district, use_area=False)
            data = fetch_overpass(query_global)
            geometry = overpass_response_to_geojson(data)
        if not geometry:
            return {
                "success": False,
                "error": "No administrative boundary relation found",
                "api_404": True,
                "api_empty_response": False,
            }
        return {
            "success": True,
            "geometry": geometry,
            "api_404": False,
            "api_empty_response": False,
        }
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return {
                "success": False,
                "error": "Overpass: boundary not found",
                "api_404": True,
                "api_empty_response": False,
            }
        return {
            "success": False,
            "error": str(e),
            "api_404": False,
            "api_empty_response": False,
        }
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": str(e),
            "api_404": False,
            "api_empty_response": False,
        }
    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
            "api_404": False,
            "api_empty_response": False,
        }
