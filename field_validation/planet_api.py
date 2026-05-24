"""
Planet Data API v1 quick-search (PSScene / PlanetScope) — metadata only by default.

No pipeline changes. Requires PLANET_API_KEY in environment.

API reference: https://developers.planet.com/docs/apis/data/
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

PLANET_DATA_API = "https://api.planet.com/data/v1"


def _headers() -> dict[str, str]:
    key = (os.environ.get("PLANET_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("PLANET_API_KEY is not set")
    return {"Authorization": f"api-key {key}", "Content-Type": "application/json"}


def geometry_filter(geojson_polygon: dict[str, Any]) -> dict[str, Any]:
    """Planet GeometryFilter expects GeoJSON geometry (Polygon or MultiPolygon)."""
    gtype = geojson_polygon.get("type")
    coords = geojson_polygon.get("coordinates")
    if gtype not in ("Polygon", "MultiPolygon") or not coords:
        raise ValueError(f"Expected Polygon/MultiPolygon GeoJSON, got type={gtype!r}")
    return {
        "type": "GeometryFilter",
        "field_name": "geometry",
        "config": {"type": gtype, "coordinates": coords},
    }


def date_range_filter_acquired(start: date, end: date) -> dict[str, Any]:
    """Inclusive calendar bounds in UTC (Planet uses ISO8601)."""
    gte = datetime(start.year, start.month, start.day, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    end_ex = end + timedelta(days=1)
    lt = datetime(end_ex.year, end_ex.month, end_ex.day, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "type": "DateRangeFilter",
        "field_name": "acquired",
        "config": {"gte": gte, "lt": lt},
    }


def and_filter(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"type": "AndFilter", "config": list(parts)}


def quick_search_items(
    item_types: list[str],
    filter_spec: dict[str, Any],
    *,
    limit: int = 250,
    session: Optional[requests.Session] = None,
) -> list[dict[str, Any]]:
    """
    POST quick-search; follow _links._next (GET) until exhausted or limit reached.
    """
    sess = session or requests.Session()
    hdr = _headers()
    post_url = f"{PLANET_DATA_API}/quick-search"
    body = {"item_types": item_types, "filter": filter_spec}
    features: list[dict[str, Any]] = []
    next_url: Optional[str] = post_url
    first = True
    max_pages = 80

    for _ in range(max_pages):
        if not next_url:
            break
        if first:
            r = sess.post(next_url, headers=hdr, json=body, timeout=120)
            first = False
        else:
            r = sess.get(next_url, headers=hdr, timeout=120)
        if r.status_code == 401:
            raise RuntimeError("Planet API 401: invalid or missing PLANET_API_KEY")
        r.raise_for_status()
        payload = r.json()
        feats = payload.get("features") or []
        features.extend(feats)
        if len(features) >= limit:
            return features[:limit]
        links = payload.get("_links") or {}
        next_url = links.get("_next") or links.get("next")
    return features[:limit]


def _simplify_planet_feature(f: dict[str, Any]) -> dict[str, Any]:
    props = f.get("properties") or {}
    item_id = f.get("id") or props.get("item_id") or props.get("id")
    acquired = props.get("acquired") or props.get("published")
    out: dict[str, Any] = {
        "planet_item_id": item_id,
        "planet_item_type": props.get("item_type"),
        "acquired": acquired,
        "cloud_cover": props.get("cloud_cover"),
        "visible_confidence": props.get("visible_confidence"),
        "clear_confidence": props.get("clear_confidence"),
        "satellite_azimuth": props.get("satellite_azimuth"),
        "satellite_id": props.get("satellite_id"),
        "instrument": props.get("instrument"),
        "pixel_resolution": props.get("pixel_resolution"),
        "strip_id": props.get("strip_id"),
        "provider": props.get("provider"),
        "ground_control": props.get("ground_control"),
        "quality_category": props.get("quality_category"),
        "anomalous_pixels": props.get("anomalous_pixels"),
        "cloud_percent": props.get("cloud_percent"),
        "heavy_haze_percent": props.get("heavy_haze_percent"),
        "light_haze_percent": props.get("light_haze_percent"),
        "shadow_percent": props.get("shadow_percent"),
        "snow_ice_percent": props.get("snow_ice_percent"),
        "view_angle": props.get("view_angle"),
    }
    return {k: v for k, v in out.items() if v is not None}


def search_pscenes_for_aoi(
    geojson_polygon: dict[str, Any],
    start: date,
    end: date,
    *,
    item_types: Optional[list[str]] = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    itypes = item_types or ["PSScene"]
    flt = and_filter(geometry_filter(geojson_polygon), date_range_filter_acquired(start, end))
    raw = quick_search_items(itypes, flt, limit=limit)
    return [_simplify_planet_feature(f) for f in raw]


def daily_calendar_with_planet(
    geojson_polygon: dict[str, Any],
    start: date,
    end: date,
    *,
    item_types: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """
    One row per calendar day in [start, end]. No interpolation.
    planet_status = MISSING | HAS_SCENE; best scene = lowest cloud_cover if available.
    """
    scenes = search_pscenes_for_aoi(geojson_polygon, start, end, item_types=item_types, limit=2000)
    by_day: dict[date, list[dict[str, Any]]] = {}
    for s in scenes:
        acq = s.get("acquired")
        if not acq:
            continue
        try:
            d = date.fromisoformat(str(acq)[:10])
        except ValueError:
            continue
        by_day.setdefault(d, []).append(s)

    rows: list[dict[str, Any]] = []
    d = start
    while d <= end:
        day_scenes = by_day.get(d, [])
        if not day_scenes:
            rows.append(
                {
                    "calendar_date": d.isoformat(),
                    "planet_status": "MISSING",
                    "planet_scene_count": 0,
                    "planet_best_item_id": None,
                    "planet_best_cloud_cover": None,
                    "planet_note": "No PSScene acquired on this calendar day (UTC date of acquired)",
                    "planet_pipeline_note": planet_data_pipeline_note(),
                }
            )
        else:

            def sort_key(x: dict[str, Any]) -> tuple:
                cc = x.get("cloud_cover")
                if cc is None:
                    return (1, 999.0)
                try:
                    return (0, float(cc))
                except (TypeError, ValueError):
                    return (1, 999.0)

            best = min(day_scenes, key=sort_key)
            rows.append(
                {
                    "calendar_date": d.isoformat(),
                    "planet_status": "HAS_SCENE",
                    "planet_scene_count": len(day_scenes),
                    "planet_best_item_id": best.get("planet_item_id"),
                    "planet_best_cloud_cover": best.get("cloud_cover"),
                    "planet_best_acquired": best.get("acquired"),
                    "planet_metadata_json": best,
                    "planet_pipeline_note": planet_data_pipeline_note(),
                }
            )
        d += timedelta(days=1)
    return rows


def list_item_assets(item_type: str, item_id: str) -> dict[str, Any]:
    sess = requests.Session()
    url = f"{PLANET_DATA_API}/item-types/{item_type}/items/{item_id}/assets/"
    r = sess.get(url, headers=_headers(), timeout=60)
    r.raise_for_status()
    return r.json()


_ASSET_KEYS_CACHE: dict[str, list[str]] = {}


def get_asset_keys_for_item(item_type: str, item_id: str) -> list[str]:
    """
    Top-level keys = Planet asset product IDs (e.g. ortho_analytic_4b, ortho_analytic_8b_sr, udm2, visual).
    This is NOT pixel data — activation + download is separate (Orders API).
    """
    cache_key = f"{item_type}:{item_id}"
    if cache_key in _ASSET_KEYS_CACHE:
        return _ASSET_KEYS_CACHE[cache_key]
    payload = list_item_assets(item_type, item_id)
    if not isinstance(payload, dict):
        _ASSET_KEYS_CACHE[cache_key] = []
        return []
    keys = sorted(payload.keys())
    _ASSET_KEYS_CACHE[cache_key] = keys
    return keys


def enrich_rows_with_planet_asset_keys(
    rows: list[dict[str, Any]],
    *,
    item_type: str = "PSScene",
    max_lookups: int = 200,
) -> list[dict[str, Any]]:
    """Attach planet_available_asset_keys CSV to HAS_SCENE rows (best-effort)."""
    n = 0
    for row in rows:
        if row.get("planet_status") != "HAS_SCENE":
            row["planet_available_asset_keys"] = None
            row["planet_asset_api_note"] = None
            continue
        iid = row.get("planet_best_item_id")
        if not iid or n >= max_lookups:
            row["planet_available_asset_keys"] = None
            row["planet_asset_api_note"] = "skipped_max_lookups_or_no_item_id" if n >= max_lookups else None
            continue
        try:
            keys = get_asset_keys_for_item(item_type, str(iid))
            row["planet_available_asset_keys"] = ",".join(keys)
            row["planet_asset_api_note"] = (
                "Keys are asset product IDs from Data API /assets/ (not DN/SR values). "
                "Raster retrieval requires activation + download (Orders API)."
            )
            n += 1
        except Exception as e:
            row["planet_available_asset_keys"] = None
            row["planet_asset_api_note"] = f"assets_request_failed: {e}"
    return rows


def planet_scenes_observation_rows(
    geojson_polygon: dict[str, Any],
    start: date,
    end: date,
    *,
    item_types: Optional[list[str]] = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """
    One row per PSScene item (actual acquired observations only). No interpolation, no gap-filled calendar.
    Uses the same quick-search as search_pscenes_for_aoi but returns one row per feature.
    """
    itypes = item_types or ["PSScene"]
    flt = and_filter(geometry_filter(geojson_polygon), date_range_filter_acquired(start, end))
    raw_features = quick_search_items(itypes, flt, limit=limit)
    rows: list[dict[str, Any]] = []
    for f in raw_features:
        simp = _simplify_planet_feature(f)
        acq = simp.get("acquired")
        cal = str(acq)[:10] if acq else None
        rows.append(
            {
                "calendar_date": cal,
                "planet_acquisition_datetime_utc": acq,
                "planet_status": "HAS_SCENE",
                "planet_scene_count": 1,
                "planet_best_item_id": simp.get("planet_item_id"),
                "planet_best_cloud_cover": simp.get("cloud_cover"),
                "planet_best_acquired": acq,
                "planet_metadata_json": simp,
                "planet_note": "One row per Planet item from quick-search (metadata only)",
                "planet_pipeline_note": planet_data_pipeline_note(),
            }
        )
    rows.sort(key=lambda r: (r.get("planet_acquisition_datetime_utc") or ""))
    return rows


def planet_data_pipeline_note() -> str:
    return (
        "quick-search returns scene metadata only. "
        "Raw bands (Blue, Green, Red, NIR, RedEdge, SWIR) are delivered as GeoTIFF assets "
        "(e.g. ortho_analytic_4b, ortho_analytic_8b_sr) after activation via Orders API — not fetched here."
    )
