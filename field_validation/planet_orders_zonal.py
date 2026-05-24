"""
Planet Orders API v2: clipped PSScene → GeoTIFF → zonal band means + indices.

Requires PLANET_API_KEY, rasterio, shapely. Consumes Planet quota / order capacity.
See: https://docs.planet.com/develop/apis/orders/mechanics/
"""

from __future__ import annotations

import logging
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

import requests

from field_validation.raster_indices import zonal_mean_bands_4planet_analytic

logger = logging.getLogger(__name__)

ORDERS_V2 = "https://api.planet.com/compute/ops/orders/v2"


def _headers() -> dict[str, str]:
    key = (os.environ.get("PLANET_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("PLANET_API_KEY is not set")
    return {"Authorization": f"api-key {key}", "Content-Type": "application/json"}


def geojson_to_clip_polygon(aoi: dict[str, Any]) -> dict[str, Any]:
    """
    Planet clip tool expects a GeoJSON Polygon. Uses largest polygon if MultiPolygon.
    """
    try:
        from shapely.geometry import mapping, shape

        g = shape(aoi)
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda x: x.area)
        elif g.geom_type == "Polygon":
            pass
        else:
            g = g.buffer(0)
        if g.geom_type != "Polygon":
            g = g.convex_hull
        return mapping(g)  # type: ignore[no-any-return]
    except Exception:
        # Fallback: wrap coordinates if already Polygon-like
        t = aoi.get("type")
        if t == "Polygon":
            return aoi
        raise ValueError(f"Cannot derive Polygon from AOI type={t!r}")


def create_clipped_scene_order(
    item_id: str,
    clip_aoi: dict[str, Any],
    *,
    product_bundle: str = "analytic_udm2",
    order_name: str = "seediq_field_clip",
) -> str:
    """Returns order_id."""
    body: dict[str, Any] = {
        "name": f"{order_name}_{item_id}",
        "source_type": "scenes",
        "products": [
            {
                "item_ids": [item_id],
                "item_type": "PSScene",
                "product_bundle": product_bundle,
            }
        ],
        # Planet API expects each tool as its own object: {"clip": {"aoi": ...}}, not {"type":"clip",...}
        "tools": [{"clip": {"aoi": clip_aoi}}],
    }
    r = requests.post(ORDERS_V2, headers=_headers(), json=body, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"Orders API {r.status_code}: {r.text[:2000]}")
    data = r.json()
    oid = data.get("id") or data.get("_id")
    if not oid:
        raise RuntimeError(f"Orders response missing id: {data!r}")
    return str(oid)


def poll_order_until_done(order_id: str, *, max_wait_s: float = 900.0, interval_s: float = 5.0) -> dict[str, Any]:
    """Poll GET order until success/failed or timeout."""
    deadline = time.monotonic() + max_wait_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        r = requests.get(f"{ORDERS_V2}/{order_id}", headers=_headers(), timeout=120)
        r.raise_for_status()
        last = r.json()
        state = (last.get("state") or last.get("status") or "").lower()
        if state in ("success", "failed", "partial"):
            return last
        time.sleep(interval_s)
    raise TimeoutError(f"Order {order_id} not finished after {max_wait_s}s; last={last!r}")


def _download_to_file(url: str, dest: Path) -> None:
    r = requests.get(url, timeout=600, stream=True)
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)


def download_order_results(order_payload: dict[str, Any], work_dir: Path) -> list[Path]:
    """
    Download all result assets (zip or tif) from a successful order GET payload.
    Returns local file paths.
    """
    results = order_payload.get("results") or []
    if not results:
        logger.warning(
            "Order GET has empty results; keys=%s",
            list(order_payload.keys())[:40],
        )
    out: list[Path] = []
    work_dir.mkdir(parents=True, exist_ok=True)
    for i, res in enumerate(results):
        if not isinstance(res, dict):
            continue
        loc = res.get("location") or res.get("download_url")
        if not loc:
            continue
        name = (res.get("name") or f"asset_{i}").replace("/", "_")
        dest = work_dir / name
        logger.info("Downloading order result %s -> %s", name, dest)
        _download_to_file(str(loc), dest)
        out.append(dest)
    return out


def pick_analytic_geotiff(work_dir: Path) -> Optional[Path]:
    """Find ortho_analytic 4b tif inside directory or extracted zip."""
    for pat in ("**/*ortho_analytic_4b*.tif", "**/*ortho_analytic*4b*.tif", "**/*.tif"):
        hits = sorted(work_dir.glob(pat))
        if hits:
            # Prefer 4b analytic
            for h in hits:
                if "4b" in h.name.lower() or "analytic" in h.name.lower():
                    return h
            return hits[0]
    return None


def extract_zips(work_dir: Path) -> None:
    for z in list(work_dir.glob("*.zip")):
        try:
            with zipfile.ZipFile(z, "r") as zf:
                zf.extractall(work_dir / z.stem)
        except zipfile.BadZipFile:
            logger.warning("Bad zip: %s", z)


def zonal_stats_for_item(
    item_id: str,
    geojson_polygon: dict[str, Any],
    work_root: Path,
    *,
    product_bundle: str = "analytic_udm2",
) -> dict[str, Any]:
    """
    Full pipeline: order clipped scene → download → zonal means + indices.
    """
    clip = geojson_to_clip_polygon(geojson_polygon)
    oid = create_clipped_scene_order(item_id, clip, product_bundle=product_bundle)
    payload = poll_order_until_done(oid)
    state = (payload.get("state") or payload.get("status") or "").lower()
    if state == "failed":
        return {
            "planet_zonal_error": payload.get("last_message") or "order_failed",
            "planet_order_id": oid,
        }
    wdir = work_root / f"order_{oid}"
    wdir.mkdir(parents=True, exist_ok=True)
    paths = download_order_results(payload, wdir)
    for p in paths:
        if p.suffix.lower() == ".zip":
            extract_zips(wdir)
    tif = pick_analytic_geotiff(wdir)
    if not tif or not tif.is_file():
        return {
            "planet_zonal_error": "no_analytic_tif_in_order_output",
            "planet_order_id": oid,
            "planet_downloaded_files": [str(p) for p in paths],
        }
    stats = zonal_mean_bands_4planet_analytic(tif, geojson_polygon)
    stats["planet_order_id"] = oid
    stats["planet_zonal_tif"] = str(tif)
    stats["planet_raw_blue"] = stats.get("blue_mean")
    stats["planet_raw_green"] = stats.get("green_mean")
    stats["planet_raw_red"] = stats.get("red_mean")
    stats["planet_raw_nir"] = stats.get("nir_mean")
    stats["planet_raw_bands_note"] = (
        f"Zonal mean reflectance from Orders clip bundle={product_bundle!r}; "
        f"band order B,G,R,NIR per Planet 4b analytic."
    )
    return stats


def enrich_rows_with_zonal_for_items(
    rows: list[dict[str, Any]],
    geojson_polygon: dict[str, Any],
    work_root: Path,
    item_ids: list[str],
    *,
    product_bundle: str = "analytic_udm2",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    For each unique item_id in item_ids, compute zonal stats and merge into every row
    whose planet_best_item_id matches.
    Returns (rows, stats) with order success/fail counts.
    """
    done: dict[str, dict[str, Any]] = {}
    stats: dict[str, Any] = {
        "orders_attempted": 0,
        "orders_succeeded": 0,
        "orders_failed": 0,
        "succeeded_item_ids": [],
        "failed_item_ids": [],
        "errors": [],
    }
    for iid in item_ids:
        if not iid:
            continue
        stats["orders_attempted"] += 1
        try:
            done[iid] = zonal_stats_for_item(
                str(iid), geojson_polygon, work_root, product_bundle=product_bundle
            )
            if done[iid].get("planet_zonal_error"):
                stats["orders_failed"] += 1
                stats["failed_item_ids"].append(str(iid))
                stats["errors"].append(f"{iid}: {done[iid].get('planet_zonal_error')}")
            else:
                stats["orders_succeeded"] += 1
                stats["succeeded_item_ids"].append(str(iid))
        except Exception as e:
            logger.exception("Zonal failed for item %s: %s", iid, e)
            err = str(e)
            done[iid] = {"planet_zonal_error": err}
            stats["orders_failed"] += 1
            stats["failed_item_ids"].append(str(iid))
            stats["errors"].append(f"{iid}: {err}")

    for row in rows:
        iid = row.get("planet_best_item_id")
        if not iid or str(iid) not in done:
            continue
        upd = done[str(iid)]
        for k, v in upd.items():
            row[k] = v
    return rows, stats
