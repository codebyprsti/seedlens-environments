"""
CDSE STAC / Sentinel Hub Catalog — scene metadata for product_id linkage.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

CDSE_STAC_SEARCH = "https://stac.dataspace.copernicus.eu/v1/search"
SH_CATALOG_SEARCH = "https://sh.dataspace.copernicus.eu/catalog/v1/search"

COLLECTION_S2 = "sentinel-2-l2a"
COLLECTION_S1 = "sentinel-1-grd"
COLLECTION_S3 = "sentinel-3-slstr"


def _bbox_from_geojson(geojson: dict) -> list[float]:
    from shapely.geometry import shape

    b = shape(geojson).bounds
    return [float(b[0]), float(b[1]), float(b[2]), float(b[3])]


def _parse_stac_item(feature: dict) -> dict[str, Any]:
    props = feature.get("properties") or {}
    dt = props.get("datetime") or feature.get("properties", {}).get("start_datetime")
    sensing = None
    if dt:
        try:
            sensing = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
        except ValueError:
            pass
    cloud = props.get("eo:cloud_cover")
    if cloud is None:
        cloud = props.get("cloudCover")
    return {
        "product_id": feature.get("id"),
        "collection_id": feature.get("collection"),
        "sensing_time": sensing.isoformat() if sensing else None,
        "cloud_cover_pct": float(cloud) if cloud is not None else None,
        "orbit_direction": props.get("sat:orbit_state") or props.get("orbitDirection"),
        "processing_baseline": props.get("processing:version") or props.get("processingBaseline"),
        "bbox": feature.get("bbox"),
        "geometry_footprint": feature.get("geometry"),
        "stac_properties": props,
    }


def search_stac_scenes(
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    *,
    collection: str = COLLECTION_S2,
    max_cloud_cover: float = 60.0,
    limit: int = 500,
    use_sh_catalog: bool = True,
) -> list[dict[str, Any]]:
    """
    Search catalogue for scenes intersecting polygon and date range.
    Returns normalized metadata dicts (one per STAC item).
    """
    try:
        import requests
    except ImportError:
        logger.warning("requests not installed; STAC search skipped")
        return []

    bbox = _bbox_from_geojson(geometry_geojson)
    end_exclusive = (date.fromisoformat(end_date[:10]) + timedelta(days=1)).isoformat()
    datetime_range = f"{start_date}T00:00:00Z/{end_exclusive}T00:00:00Z"

    body: dict[str, Any] = {
        "collections": [collection],
        "bbox": bbox,
        "datetime": datetime_range,
        "limit": min(limit, 500),
    }
    if collection == COLLECTION_S2:
        body["query"] = {"eo:cloud_cover": {"lte": max_cloud_cover}}

    url = SH_CATALOG_SEARCH if use_sh_catalog else CDSE_STAC_SEARCH
    scenes: list[dict[str, Any]] = []
    try:
        resp = requests.post(url, json=body, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        for feat in data.get("features") or []:
            parsed = _parse_stac_item(feat)
            if parsed.get("product_id"):
                ad = None
                st = parsed.get("sensing_time")
                if st:
                    ad = str(st)[:10]
                parsed["acquisition_date"] = ad
                scenes.append(parsed)
    except Exception as e:
        logger.warning("STAC search failed (%s): %s", url, e)
        if use_sh_catalog:
            return search_stac_scenes(
                geometry_geojson,
                start_date,
                end_date,
                collection=collection,
                max_cloud_cover=max_cloud_cover,
                limit=limit,
                use_sh_catalog=False,
            )
    return scenes


def scenes_by_acquisition_date(scenes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Pick lowest-cloud scene per calendar day."""
    by_date: dict[str, list[dict]] = {}
    for s in scenes:
        ad = s.get("acquisition_date")
        if not ad:
            continue
        by_date.setdefault(ad, []).append(s)

    best: dict[str, dict[str, Any]] = {}
    for ad, items in by_date.items():
        items.sort(key=lambda x: (x.get("cloud_cover_pct") is None, x.get("cloud_cover_pct") or 999))
        best[ad] = items[0]
    return best


def persist_stac_scenes(
    db,
    *,
    location_id: str,
    file_name: str,
    season_id: Optional[str],
    satellite: str,
    scenes: list[dict[str, Any]],
    raw_observation_id: Optional[int] = None,
) -> int:
    """Insert STAC rows; returns count inserted."""
    from sqlalchemy import text

    n = 0
    for s in scenes:
        ad = s.get("acquisition_date")
        pid = s.get("product_id")
        if not ad or not pid:
            continue
        try:
            import json

            db.execute(
                text("""
                    INSERT INTO operations.stac_scene_catalog (
                        location_id, file_name, season_id, satellite, acquisition_date,
                        product_id, collection_id, sensing_time, cloud_cover_pct,
                        orbit_direction, processing_baseline, bbox, geometry_footprint,
                        stac_properties, raw_observation_id
                    ) VALUES (
                        :loc, :fn, :sid, :sat, CAST(:ad AS date),
                        :pid, :coll, CAST(:st AS timestamptz), :cc,
                        :orb, :pb, CAST(:bbox AS jsonb), CAST(:geom AS jsonb),
                        CAST(:props AS jsonb), :raw_id
                    )
                    ON CONFLICT (location_id, file_name, season_id, satellite, acquisition_date, product_id)
                    DO NOTHING
                """),
                {
                    "loc": location_id,
                    "fn": file_name,
                    "sid": season_id,
                    "sat": satellite,
                    "ad": ad,
                    "pid": pid,
                    "coll": s.get("collection_id"),
                    "st": s.get("sensing_time"),
                    "cc": s.get("cloud_cover_pct"),
                    "orb": s.get("orbit_direction"),
                    "pb": s.get("processing_baseline"),
                    "bbox": json.dumps(s.get("bbox")) if s.get("bbox") else None,
                    "geom": json.dumps(s.get("geometry_footprint")) if s.get("geometry_footprint") else None,
                    "props": json.dumps(s.get("stac_properties") or {}),
                    "raw_id": raw_observation_id,
                },
            )
            n += 1
        except Exception as e:
            logger.debug("stac_scene_catalog insert skip: %s", e)
    return n
