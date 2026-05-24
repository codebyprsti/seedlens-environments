#!/usr/bin/env python3
"""
Programmatic Planet API permission audit for PLANET_API_KEY (reads from environment; optional .env).

Does not print the API key. Prints HTTP status codes and JSON/text error bodies.

Usage:
  set PLANET_API_KEY=...
  python scripts/planet_api_permission_audit.py [--max-download-mb 80]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:
    pass

import requests

PLANET_DATA = "https://api.planet.com/data/v1"
ORDERS_V2 = "https://api.planet.com/compute/ops/orders/v2"


def _headers() -> dict[str, str]:
    key = (os.environ.get("PLANET_API_KEY") or "").strip()
    if not key:
        print("FATAL: PLANET_API_KEY is not set in the environment.")
        sys.exit(2)
    return {
        "Authorization": f"api-key {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _print_http(label: str, r: requests.Response, max_len: int = 12000) -> None:
    print(f"  [{label}] HTTP {r.status_code}")
    txt = r.text or ""
    if len(txt) > max_len:
        print(f"  [{label}] body (truncated): {txt[:max_len]}...")
    else:
        print(f"  [{label}] body: {txt}")


def _pick_items(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer quality_category=standard over test; keep order."""
    std = [f for f in features if (f.get("properties") or {}).get("quality_category") == "standard"]
    if std:
        return std + [f for f in features if f not in std]
    return features


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--max-download-mb", type=float, default=80.0)
    args = p.parse_args()
    max_bytes = int(args.max_download_mb * 1024 * 1024)

    summary: dict[str, str] = {}

    hdr = _headers()
    sess = requests.Session()

    # --- 1) Quick-search ---
    print("\n=== 1) Authentication / Data API quick-search ===")
    tiny_poly = {
        "type": "Polygon",
        "coordinates": [
            [
                [81.0, 21.0],
                [81.05, 21.0],
                [81.05, 21.05],
                [81.0, 21.05],
                [81.0, 21.0],
            ]
        ],
    }
    flt = {
        "type": "AndFilter",
        "config": [
            {"type": "GeometryFilter", "field_name": "geometry", "config": tiny_poly},
            {
                "type": "DateRangeFilter",
                "field_name": "acquired",
                "config": {
                    "gte": "2024-06-01T00:00:00.000Z",
                    "lt": "2026-12-31T23:59:59.999Z",
                },
            },
        ],
    }
    r1 = sess.post(
        f"{PLANET_DATA}/quick-search",
        headers=hdr,
        json={"item_types": ["PSScene"], "filter": flt},
        timeout=120,
    )
    print(f"  POST {PLANET_DATA}/quick-search")
    _print_http("quick-search", r1)

    features: list[dict[str, Any]] = []
    if r1.status_code == 200:
        features = (r1.json() or {}).get("features") or []
    if not features and r1.status_code == 200:
        wide = {
            "type": "AndFilter",
            "config": [
                {
                    "type": "GeometryFilter",
                    "field_name": "geometry",
                    "config": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [-100.0, 30.0],
                                [-99.0, 30.0],
                                [-99.0, 31.0],
                                [-100.0, 31.0],
                                [-100.0, 30.0],
                            ]
                        ],
                    },
                },
                {
                    "type": "DateRangeFilter",
                    "field_name": "acquired",
                    "config": {
                        "gte": "2024-01-01T00:00:00.000Z",
                        "lt": "2026-12-31T00:00:00.000Z",
                    },
                },
            ],
        }
        r1b = sess.post(
            f"{PLANET_DATA}/quick-search",
            headers=hdr,
            json={"item_types": ["PSScene"], "filter": wide},
            timeout=120,
        )
        print(f"\n  (retry) POST quick-search wider bbox")
        _print_http("quick-search-wide", r1b)
        if r1b.status_code == 200:
            features = (r1b.json() or {}).get("features") or []

    summary["data_api"] = "WORKING" if r1.status_code == 200 and features else (
        "NOT WORKING" if r1.status_code != 200 else "WORKING (HTTP 200 but zero features)"
    )

    if not features:
        print("\nNo PSScene features returned; cannot continue steps 2-6.")
        _final_summary(summary)
        return 1

    ranked = _pick_items(features)
    item_id = ranked[0].get("id")
    embedded_assets = ranked[0].get("assets") or []
    qc = (ranked[0].get("properties") or {}).get("quality_category")
    print(f"\n  Using primary item_id={item_id!r} quality_category={qc!r}")
    print(f"  Feature-embedded asset names (from quick-search Feature): {embedded_assets[:20]}{'...' if len(embedded_assets) > 20 else ''}")

    item_type = "PSScene"
    asset_id = "ortho_analytic_4b"

    # --- 2) Asset listing ---
    print("\n=== 2) Asset listing GET .../items/{item_id}/assets/ ===")
    assets_url = f"{PLANET_DATA}/item-types/{item_type}/items/{item_id}/assets/"
    r2 = sess.get(assets_url, headers=hdr, timeout=60)
    print(f"  GET {assets_url}")
    _print_http("assets-list", r2)
    listed_keys: list[str] = []
    if r2.status_code == 200:
        j2 = r2.json()
        if isinstance(j2, dict):
            listed_keys = sorted(k for k in j2.keys() if not k.startswith("_"))
        print(f"  Parsed keys from JSON object: {len(listed_keys)} keys")
        if listed_keys:
            print(f"  keys (sample): {', '.join(listed_keys[:30])}")
    if not listed_keys and embedded_assets:
        print("  NOTE: GET /assets/ returned no keys; using Feature.assets[] above as ground truth for names.")
    summary["asset_listing"] = "WORKING" if r2.status_code == 200 else f"NOT WORKING (HTTP {r2.status_code})"

    # --- 3) Single-asset GET + activation ---
    print(f"\n=== 3) Single asset GET + activation for {asset_id} ===")
    asset_single = f"{PLANET_DATA}/item-types/{item_type}/items/{item_id}/assets/{asset_id}"
    r2a = sess.get(asset_single, headers=hdr, timeout=60)
    print(f"  GET {asset_single}")
    _print_http("asset-single", r2a)

    activate_urls = [
        f"{asset_single}/activate",
        f"{asset_single}/activate/",
    ]
    r3: Optional[requests.Response] = None
    for au in activate_urls:
        r3 = sess.post(au, headers=hdr, timeout=60)
        print(f"  POST {au}")
        _print_http("activate", r3)
        if r3.status_code not in (404, 405):
            break

    last_activate_code = r3.status_code if r3 is not None else None
    location_url: Optional[str] = None
    status: Optional[str] = None
    for attempt in range(40):
        r3g = sess.get(asset_single, headers=hdr, timeout=60)
        if r3g.status_code != 200:
            print(f"  poll GET asset HTTP {r3g.status_code}")
            _print_http("asset-poll", r3g)
            break
        try:
            aj = r3g.json()
        except Exception:
            break
        status = aj.get("status")
        location_url = aj.get("location")
        print(f"  poll {attempt + 1}: status={status!r} has_location={bool(location_url)}")
        if status == "active" and location_url:
            break
        if status in ("failed", "canceled"):
            print(f"  activation end state: {json.dumps(aj, indent=2)[:4000]}")
            break
        time.sleep(5)

    if r3 is not None and r3.status_code in (200, 202, 204) and status == "active":
        summary["asset_activation"] = "ALLOWED"
    elif r3 is not None and r3.status_code == 403:
        summary["asset_activation"] = "NOT ALLOWED"
    elif r2a.status_code == 500 and r3 is not None and r3.status_code == 404:
        summary["asset_activation"] = "NOT ALLOWED (GET asset HTTP 500; POST activate HTTP 404 - trial/catalog vs delivery)"
    elif r3 is not None and r3.status_code == 404:
        summary["asset_activation"] = "NOT ALLOWED (activate HTTP 404)"
    else:
        summary["asset_activation"] = (
            f"UNCLEAR (activate HTTP {last_activate_code}, asset GET HTTP {r2a.status_code}, poll status={status!r})"
        )

    # --- 4) Download ---
    print("\n=== 4) Direct asset download (if location URL available) ===")
    if not location_url:
        print("  Skipped: no download location URL (asset not active).")
        summary["direct_download"] = "NOT ALLOWED / SKIPPED (no URL)"
    else:
        print(f"  GET stream (max {args.max_download_mb} MB)")
        try:
            rd = sess.get(location_url, timeout=600, stream=True)
            print(f"  download HTTP {rd.status_code}")
            if rd.status_code != 200:
                _print_http("download", rd)
                summary["direct_download"] = f"NOT WORKING (HTTP {rd.status_code})"
            else:
                total = 0
                fpath = Path(tempfile.mkstemp(suffix=".tif")[1])
                try:
                    with open(fpath, "wb") as out:
                        for chunk in rd.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                break
                            out.write(chunk)
                            total += len(chunk)
                            if total >= max_bytes:
                                print(f"  stopped at byte cap: {total}")
                                break
                    print(f"  bytes written: {total}")
                    try:
                        import rasterio

                        with rasterio.open(fpath) as src:
                            print(f"  rasterio: readable=True  band_count={src.count}  shape={src.shape}")
                        summary["direct_download"] = "ALLOWED"
                    except Exception as e:
                        print(f"  rasterio: {e}")
                        summary["direct_download"] = f"UNCLEAR (bytes saved but not readable: {e})"
                finally:
                    try:
                        fpath.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception as e:
            print(f"  download exception: {e}")
            summary["direct_download"] = f"NOT WORKING ({e})"

    # --- 5) Orders without clip ---
    print("\n=== 5) Orders API - full scene (no clip tool) ===")
    order_body = {
        "name": f"audit_no_clip_{item_id}",
        "source_type": "scenes",
        "products": [
            {
                "item_ids": [item_id],
                "item_type": item_type,
                "product_bundle": "analytic_udm2",
            }
        ],
    }
    r5 = sess.post(ORDERS_V2, headers=hdr, json=order_body, timeout=120)
    print(f"  POST {ORDERS_V2}")
    _print_http("orders-no-clip", r5)
    if r5.status_code in (200, 201, 202):
        summary["orders_api"] = "ALLOWED"
    elif r5.status_code in (400, 403):
        summary["orders_api"] = "NOT ALLOWED"
    else:
        summary["orders_api"] = f"UNCLEAR (HTTP {r5.status_code})"

    # --- 6) Clip ---
    print("\n=== 6) Orders API - clip tool (tiny AOI) ===")
    clip_aoi = {
        "type": "Polygon",
        "coordinates": [
            [
                [81.02, 21.02],
                [81.03, 21.02],
                [81.03, 21.03],
                [81.02, 21.03],
                [81.02, 21.02],
            ]
        ],
    }
    order_clip = {
        "name": f"audit_clip_{item_id}",
        "source_type": "scenes",
        "products": [
            {
                "item_ids": [item_id],
                "item_type": item_type,
                "product_bundle": "analytic_udm2",
            }
        ],
        "tools": [{"clip": {"aoi": clip_aoi}}],
    }
    r6 = sess.post(ORDERS_V2, headers=hdr, json=order_clip, timeout=120)
    print(f"  POST {ORDERS_V2} (with clip)")
    _print_http("orders-clip", r6)
    if r6.status_code in (200, 201, 202):
        summary["clip_tool"] = "ALLOWED"
    else:
        try:
            err = r6.json()
            msg = ""
            for g in err.get("general") or []:
                msg += str(g.get("message", "")) + " "
            if "permission" in msg.lower() and "clip" in msg.lower():
                summary["clip_tool"] = "NOT ALLOWED"
            elif r6.status_code == 400:
                summary["clip_tool"] = f"NOT ALLOWED / REJECTED (HTTP 400)"
            else:
                summary["clip_tool"] = f"NOT ALLOWED (HTTP {r6.status_code})"
        except Exception:
            summary["clip_tool"] = f"NOT ALLOWED (HTTP {r6.status_code})"

    _final_summary(summary)
    return 0


def _final_summary(summary: dict[str, str]) -> None:
    print("\n" + "=" * 64)
    print("SUMMARY (from HTTP results above; do not infer beyond measured responses)")
    print("=" * 64)
    print(f"  Data API:           {summary.get('data_api', 'NOT TESTED')}")
    print(f"  Asset listing:      {summary.get('asset_listing', 'NOT TESTED')}")
    print(f"  Asset activation:   {summary.get('asset_activation', 'NOT TESTED')}")
    print(f"  Direct download:    {summary.get('direct_download', 'NOT TESTED')}")
    print(f"  Orders API:         {summary.get('orders_api', 'NOT TESTED')}")
    print(f"  Clip tool:          {summary.get('clip_tool', 'NOT TESTED')}")
    print("=" * 64)

    da = summary.get("data_api", "")
    data_bin = "WORKING" if da.startswith("WORKING") else "NOT WORKING"
    aa = summary.get("asset_activation", "")
    act_bin = "ALLOWED" if aa == "ALLOWED" else "NOT ALLOWED"
    dd = summary.get("direct_download", "")
    dl_bin = "ALLOWED" if dd == "ALLOWED" else "NOT ALLOWED"
    oa = summary.get("orders_api", "")
    ord_bin = "ALLOWED" if oa == "ALLOWED" else "NOT ALLOWED"
    ct = summary.get("clip_tool", "")
    clip_bin = "ALLOWED" if ct == "ALLOWED" else "NOT ALLOWED"

    print("\nCompact (requested labels):")
    print(f"  Data API:           {data_bin}")
    print(f"  Asset Activation:   {act_bin}")
    print(f"  Direct Download:    {dl_bin}")
    print(f"  Orders API:         {ord_bin}")
    print(f"  Clip Tool:          {clip_bin}")
    print("=" * 64)
    print("\nNotes:")
    print("  - quick-search Features include 'assets' name lists; GET /assets/ may still return {}.")
    print("  - If Orders returns 'no access to assets', bundle delivery is blocked even when search lists names.")
    print("=" * 64)


if __name__ == "__main__":
    raise SystemExit(main())
