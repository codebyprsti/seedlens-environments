#!/usr/bin/env python3
"""
Standalone test script for OpenStreetMap Overpass API → GeoJSON → operations.location_polygons.

Usage:
  # Test fetch only (no DB), with built-in example
  python scripts/test_overpass_polygon.py

  # Test fetch for a location from DB, then insert one polygon
  python scripts/test_overpass_polygon.py --location-id L_500000 --insert

  # Custom village/district/state (no DB read)
  python scripts/test_overpass_polygon.py --village "Hyderabad" --district "Hyderabad" --state "Telangana"

  # Fetch from DB location and insert
  python scripts/test_overpass_polygon.py --location-id L_500000 --insert
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Overpass API → GeoJSON → location_polygons")
    parser.add_argument("--village", type=str, help="Village name (with --district and --state)")
    parser.add_argument("--district", type=str, help="District name")
    parser.add_argument("--state", type=str, help="State name")
    parser.add_argument(
        "--location-id",
        type=str,
        help="Use this location_id from operations.locations (village/district/state from DB)",
    )
    parser.add_argument(
        "--insert",
        action="store_true",
        help="Insert fetched polygon into operations.location_polygons (requires --location-id or DB row)",
    )
    parser.add_argument("--no-insert", dest="insert", action="store_false")
    args = parser.parse_args()

    village, district, state = args.village, args.district, args.state
    location_id = args.location_id

    if args.location_id and not (village and state):
        # Load location from DB
        from core.db import SessionLocal
        from sqlalchemy import text

        db = SessionLocal()
        try:
            row = db.execute(
                text(
                    "SELECT location_id, village, mandal, district, state FROM operations.locations WHERE location_id = :lid"
                ),
                {"lid": args.location_id},
            ).fetchone()
            if not row:
                logger.error("Location %s not found in operations.locations", args.location_id)
                return 1
            location_id = row[0]
            village = (row[1] or "").strip()
            district = (row[2] or row[3] or "").strip()
            state = (row[4] or "").strip()
            logger.info("From DB: location_id=%s village=%s district=%s state=%s", location_id, village, district, state)
        finally:
            db.close()

    if not village or not state:
        # Default example (OSM often has state/city boundaries; village may be sparse)
        village = "Secunderabad"
        district = "Hyderabad"
        state = "Telangana"
        logger.info("Using example: village=%s district=%s state=%s", village, district, state)

    from services.overpass_client import fetch_village_boundary
    from services.bhuvan_polygon_service import BhuvanPolygonService
    from core.db import SessionLocal

    # 1) Fetch from Overpass
    logger.info("Fetching boundary from Overpass: village=%s state=%s", village, state)
    result = fetch_village_boundary(village, state, district=district or None, use_area=True)
    if not result.get("success"):
        logger.error("Overpass fetch failed: %s", result.get("error"))
        return 1

    geometry = result["geometry"]
    logger.info("Got geometry type=%s", geometry.get("type"))

    # 2) Validate and optionally insert (service needs a Session)
    db = SessionLocal()
    try:
        svc = BhuvanPolygonService(db)
        validation = svc._validate_geometry(geometry)
        if not validation["valid"]:
            logger.error("Validation failed: %s", validation["error"])
            return 1
        logger.info("Validation passed")

        # 3) Optional insert
        if args.insert and location_id:
            location = {
                "location_id": location_id,
                "village": village,
                "mandal": district,
                "district": district,
                "state": state,
            }
            out = svc._store_polygon(location, geometry, source="overpass")
            logger.info("Insert result: %s", out)
            print("\nInserted:", json.dumps(out, indent=2))
        elif args.insert and not location_id:
            logger.error("--insert requires --location-id or a DB row (village/state from DB)")
            return 1
    finally:
        db.close()

    # Print GeoJSON summary (type + bbox hint)
    coords = geometry.get("coordinates", [])
    if geometry.get("type") == "Polygon" and coords:
        ring = coords[0]
        logger.info("Polygon ring length=%s", len(ring))
    elif geometry.get("type") == "MultiPolygon" and coords:
        logger.info("MultiPolygon: %s polygon(s)", len(coords))

    print("\nGeometry type:", geometry.get("type"))
    print("Sample (first 200 chars):", json.dumps(geometry)[:200] + "...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
