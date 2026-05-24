#!/usr/bin/env python3
"""
Fetch village polygons from Bhuvan API and store in operations.location_polygons.

- Fetches ALL locations from operations.locations (or a subset via --location-id).
- Normalizes inputs (strip, title case, collapse spaces); logs request payload.
- API strategy: coordinate (if lat/lon) -> village+mandal+district+state -> village+district+state.
- Handles FeatureCollection (iterate features) and MultiPolygon (split); validates geometry.
- DELETE existing polygons per location_id then INSERT with polygon_index; transaction-safe.
- Dry-run: no API calls, no DB writes. Single-location mode: --location-id L_xxx.

Usage:
    python scripts/fetch_bhuvan_polygons.py [options]

Options:
    --batch-size N       Process N locations per batch (default: 100)
    --skip-existing      Skip locations that already have polygons (default: True)
    --no-skip-existing   Process all locations, re-fetch and replace polygons
    --dry-run            Log only; no API calls, no DB modifications
    --location-id ID     Process only this location_id (e.g. L_500000)
    --location-ids ...   Process only these location_ids (space-separated)
    --batch-transaction  Commit per batch (faster); default is commit per location (resilient)
    --access-token TOKEN Custom Bhuvan access token
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.db import SessionLocal
from services.bhuvan_polygon_service import BhuvanPolygonService
from services.polygon_sync_orchestrator import PolygonSyncOrchestrator

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/bhuvan_polygon_fetch.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch village polygons from Bhuvan API and store in operations.location_polygons",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Locations per batch (default: 100)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip locations that already have polygons (default: True)",
    )
    parser.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Process all locations, replace existing polygons",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No API calls, no DB writes; only log what would be done",
    )
    parser.add_argument(
        "--location-id",
        type=str,
        default=None,
        help="Process only this location_id (e.g. L_500000)",
    )
    parser.add_argument(
        "--location-ids",
        type=str,
        nargs="*",
        default=None,
        help="Process only these location_ids (space-separated)",
    )
    parser.add_argument(
        "--batch-transaction",
        action="store_true",
        help="Commit per batch (faster). Default: commit per location (resilient).",
    )
    parser.add_argument(
        "--access-token",
        type=str,
        default=None,
        help="Custom Bhuvan API access token",
    )
    parser.add_argument(
        "--overpass",
        action="store_true",
        default=True,
        help="Use OpenStreetMap Overpass API (default).",
    )
    parser.add_argument(
        "--bhuvan",
        dest="overpass",
        action="store_false",
        help="Use Bhuvan API instead of Overpass.",
    )
    args = parser.parse_args()

    location_ids = args.location_ids
    if args.location_id:
        location_ids = [args.location_id]

    logger.info("=" * 80)
    logger.info("Polygon Fetch (PolygonSyncOrchestrator) - %s", "Overpass" if args.overpass else "Bhuvan")
    logger.info("=" * 80)
    logger.info("batch_size=%s skip_existing=%s dry_run=%s", args.batch_size, args.skip_existing, args.dry_run)
    logger.info("location_ids=%s use_batch_transaction=%s source=%s", location_ids or "all", args.batch_transaction, "overpass" if args.overpass else "bhuvan")
    logger.info("=" * 80)

    db = None
    polygon_service = None
    try:
        db = SessionLocal()
        from services.bhuvan_polygon_service import ACCESS_TOKEN
        token = args.access_token or ACCESS_TOKEN
        polygon_service = BhuvanPolygonService(db, access_token=token)

        orchestrator = PolygonSyncOrchestrator(db, polygon_service=polygon_service)
        stats = orchestrator.sync_polygons(
            batch_size=args.batch_size,
            skip_existing=args.skip_existing,
            dry_run=args.dry_run,
            location_ids=location_ids,
            use_batch_transaction=args.batch_transaction,
            fetch_all_locations=True,
            use_overpass=args.overpass,
        )

        # Final summary (required format)
        summary = {
            "total_locations": stats.get("total_locations", 0),
            "successful": stats.get("successful", 0),
            "failed": stats.get("failed", 0),
            "polygons_inserted": stats.get("polygons_inserted", 0),
            "api_404_count": stats.get("api_404_count", 0),
            "api_empty_response_count": stats.get("api_empty_response_count", 0),
            "skipped": stats.get("skipped", 0),
            "duplicates": stats.get("duplicates", 0),
            "count_location_polygons_after": stats.get("count_location_polygons_after"),
        }
        print("\n" + "=" * 80)
        print("SUMMARY (JSON)")
        print("=" * 80)
        print(json.dumps(summary, indent=2))
        print("=" * 80)

        if stats.get("errors"):
            print(f"\nFirst 10 errors ({len(stats['errors'])} total):")
            for err in stats["errors"][:10]:
                print(f"  {err.get('location_id')}: {err.get('error')}")

        return 0 if stats.get("failed", 0) == 0 else 1
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as e:
        logger.exception("Critical error: %s", e)
        return 1
    finally:
        if polygon_service:
            polygon_service.close()
        if db:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
