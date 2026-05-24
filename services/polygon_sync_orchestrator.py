"""
Polygon Sync Orchestrator

Orchestrates the end-to-end flow for syncing location polygons:
1. Read required fields from operations.locations (via BhuvanPolygonService)
2. Call Polygon API for each location (via BhuvanPolygonService)
3. Transform API response into DB format (parsing/validation inside service)
4. Insert or update records in operations.location_polygons (batch or single)

This module does not duplicate business logic; it reuses BhuvanPolygonService
for locations, API fetch, response parsing, and storage.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import text

from core.db import get_connection, release_connection
from services.bhuvan_polygon_service import BhuvanPolygonService, DEBUG_READ_PATH

logger = logging.getLogger(__name__)

# Rate limiting: sleep between API calls (seconds)
API_CALL_DELAY = 0.5

# Debug SQL to manually verify recent inserts:
#   SELECT location_id FROM operations.location_polygons ORDER BY created_at DESC LIMIT 10;


class PolygonSyncOrchestrator:
    """
    Orchestrator for the location-polygon sync flow.
    Composes BhuvanPolygonService only; no direct DB or API logic here.
    """

    def __init__(self, db: Session, polygon_service: Optional[BhuvanPolygonService] = None):
        """
        Args:
            db: SQLAlchemy session (passed through to polygon service).
            polygon_service: Optional existing BhuvanPolygonService; if None, one is created.
        """
        self.db = db
        self.polygon_service = polygon_service or BhuvanPolygonService(db)

    def sync_polygons(
        self,
        batch_size: int = 100,
        skip_existing: bool = True,
        dry_run: bool = False,
        location_ids: Optional[List[str]] = None,
        use_batch_transaction: bool = False,
        fetch_all_locations: bool = True,
        use_overpass: bool = True,
    ) -> Dict[str, Any]:
        """
        Run the full sync: get locations -> fetch from API -> store polygons.
        Transaction-safe: commit per location when use_batch_transaction=False (default).

        Args:
            batch_size: Number of locations per batch (for progress logging).
            skip_existing: If True, skip locations that already have polygons.
            dry_run: If True, no API calls and no DB writes.
            location_ids: If set, only sync these location_id values.
            use_batch_transaction: If True, commit per batch; if False, commit per location (resilient).
            fetch_all_locations: If True, fetch ALL rows from operations.locations (no DISTINCT).
            use_overpass: If True, use OpenStreetMap Overpass API; if False, use Bhuvan API.

        Returns:
            Stats including total_locations, successful, failed, polygons_inserted,
            api_404_count, api_empty_response_count, count_location_polygons_after.
        """
        polygon_source = "overpass" if use_overpass else "bhuvan"
        logger.info(
            "Polygon sync started: batch_size=%s, skip_existing=%s, dry_run=%s, "
            "location_ids=%s, use_batch_transaction=%s, fetch_all=%s, source=%s",
            batch_size, skip_existing, dry_run,
            f"{len(location_ids)} ids" if location_ids else "all",
            use_batch_transaction,
            fetch_all_locations,
            polygon_source,
        )
        stats = {
            "total_processed": 0,
            "successful": 0,
            "failed": 0,
            "skipped": 0,
            "duplicates": 0,
            "errors": [],
            "total_locations": 0,
            "polygons_fetched": 0,
            "polygons_inserted": 0,
            "polygons_skipped": 0,
            "errors_count": 0,
            "api_404_count": 0,
            "api_empty_response_count": 0,
            "count_location_polygons_after": None,
        }

        try:
            locations = self.polygon_service.get_locations_for_polygon_sync(
                skip_existing=skip_existing,
                location_ids=location_ids,
                fetch_all=fetch_all_locations,
            )
            total = len(locations)
            stats["total_locations"] = total
            logger.info("Retrieved total_locations=%s for polygon sync", total)
            if total == 0:
                _log_final_counts(self.db, stats, dry_run)
                return stats

            for batch_start in range(0, total, batch_size):
                batch_end = min(batch_start + batch_size, total)
                batch = locations[batch_start:batch_end]
                batch_to_store: List[tuple] = []  # (location, geometry)

                for location in batch:
                    stats["total_processed"] += 1

                    if not location.get("village") or not location.get("district"):
                        logger.warning(
                            "Skipping location %s: village or district missing (lat=%s lon=%s)",
                            location.get("location_id"),
                            location.get("latitude"),
                            location.get("longitude"),
                        )
                        stats["skipped"] += 1
                        continue

                    if self.polygon_service._polygon_exists(location["location_id"]):
                        stats["duplicates"] += 1
                        continue

                    if dry_run:
                        logger.info(
                            "[DRY RUN] Would fetch polygon for %s, %s, %s, %s",
                            location.get("village"),
                            location.get("mandal"),
                            location.get("district"),
                            location.get("state"),
                        )
                        stats["successful"] += 1
                        continue

                    fetch_fn = (
                        self.polygon_service._fetch_polygon_from_overpass
                        if use_overpass
                        else self.polygon_service._fetch_polygon_from_api
                    )
                    result = fetch_fn(location)
                    if result.get("success") and result.get("geometry"):
                        if use_batch_transaction:
                            batch_to_store.append((location, result["geometry"]))
                        else:
                            logger.info(
                                "[DEBUG] Storing location_id=%s (_polygon_exists was False)",
                                location["location_id"],
                            )
                            out = self.polygon_service._store_polygon(
                                location, result["geometry"], source=polygon_source
                            )
                            stats["polygons_inserted"] += out["polygons_inserted"]
                        stats["successful"] += 1
                        logger.debug(
                            "Fetched polygon for location_id=%s",
                            location["location_id"],
                        )
                    else:
                        stats["failed"] += 1
                        if result.get("api_404"):
                            stats["api_404_count"] += 1
                        if result.get("api_empty_response"):
                            stats["api_empty_response_count"] += 1
                        err = result.get("error", "Unknown error")
                        stats["errors"].append({
                            "location_id": location["location_id"],
                            "village": location.get("village"),
                            "district": location.get("district"),
                            "error": err,
                        })
                        logger.warning(
                            "Failed to fetch polygon for location_id=%s: %s (api_404=%s api_empty=%s)",
                            location["location_id"],
                            err,
                            result.get("api_404", False),
                            result.get("api_empty_response", False),
                        )

                    if not dry_run:
                        time.sleep(API_CALL_DELAY)

                if use_batch_transaction and batch_to_store and not dry_run:
                    try:
                        logger.info(
                            "[DEBUG] Batch storing location_ids=%s (_polygon_exists was False for all)",
                            [loc["location_id"] for loc, _ in batch_to_store],
                        )
                        inserted = self.polygon_service.store_polygons_batch(
                            batch_to_store, source=polygon_source
                        )
                        stats["polygons_inserted"] += inserted
                        if inserted == 0:
                            logger.debug(
                                "Batch store returned 0 rows inserted for %s item(s) (ON CONFLICT)",
                                len(batch_to_store),
                            )
                    except Exception as e:
                        logger.exception("Batch store failed: %s", e)
                        for loc, _ in batch_to_store:
                            stats["failed"] += 1
                            stats["successful"] -= 1
                            stats["errors"].append({
                                "location_id": loc["location_id"],
                                "village": loc.get("village"),
                                "error": str(e),
                            })

                stats["polygons_fetched"] = stats["successful"]
                stats["polygons_skipped"] = stats["skipped"] + stats["duplicates"]

                logger.info(
                    "Batch %s–%s done: successful=%s, failed=%s, skipped=%s, duplicates=%s, "
                    "polygons_inserted=%s, api_404=%s, api_empty=%s",
                    batch_start + 1,
                    batch_end,
                    stats["successful"],
                    stats["failed"],
                    stats["skipped"],
                    stats["duplicates"],
                    stats["polygons_inserted"],
                    stats["api_404_count"],
                    stats["api_empty_response_count"],
                )

            stats["polygons_fetched"] = stats["successful"]
            stats["polygons_skipped"] = stats["skipped"] + stats["duplicates"]
            stats["errors_count"] = len(stats["errors"])

            if stats["polygons_inserted"] == 0 and not dry_run and stats["total_processed"] > 0:
                logger.warning(
                    "Polygon sync completed but polygons_inserted=0 (total_processed=%s, successful=%s). "
                    "Check ON CONFLICT / skip_existing or API responses.",
                    stats["total_processed"],
                    stats["successful"],
                )

            _log_final_counts(self.db, stats, dry_run)
            return stats
        except Exception as e:
            logger.exception("Polygon sync failed: %s", e)
            raise


def _log_read_path_context(db: Session) -> None:
    """Log current_database(), current_schema(), search_path for read-path verification."""
    if not DEBUG_READ_PATH:
        return
    try:
        row = db.execute(text(
            "SELECT current_database(), current_schema(), current_setting('search_path'), "
            "inet_server_addr()::text"
        )).fetchone()
        db_name = row[0] if row else "?"
        schema = row[1] if row and len(row) > 1 else "?"
        search_path = row[2] if row and len(row) > 2 else "?"
        server_addr = row[3] if row and len(row) > 3 else "?"
        logger.info(
            "[DEBUG READ PATH] Connection context: database=%s schema=%s search_path=%s "
            "inet_server_addr=%s (this is the connection used for count_location_polygons_after)",
            db_name, schema, search_path, server_addr,
        )
    except Exception as e:
        logger.warning("[DEBUG READ PATH] Could not get connection context: %s", e)


def _log_final_counts(db: Session, stats: Dict[str, Any], dry_run: bool) -> None:
    """Run SELECT COUNT(*) on location_polygons and log + set count_location_polygons_after.
    Uses SQLAlchemy Session (self.db). Also runs same COUNT on psycopg2 pool to compare."""
    try:
        _log_read_path_context(db)  # logs current_database(), inet_server_addr(), etc.
        result = db.execute(text("SELECT COUNT(*) FROM operations.location_polygons"))
        count = result.scalar() or 0
        stats["count_location_polygons_after"] = count
        logger.info(
            "Final validation (Session): count_location_polygons_after=%s (dry_run=%s)",
            count, dry_run,
        )
        # Cross-check: same COUNT on the pool connection (used for inserts)
        try:
            conn = get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT current_database(), inet_server_addr()::text")
                row = cur.fetchone()
                pool_db, pool_addr = (row[0], row[1]) if row else ("?", "?")
                cur.execute("SELECT COUNT(*) FROM operations.location_polygons")
                pool_count = (cur.fetchone() or (0,))[0]
                cur.close()
                logger.info(
                    "Final validation (pool): database=%s inet_server_addr=%s count=%s",
                    pool_db, pool_addr, pool_count,
                )
                if count != pool_count:
                    logger.warning(
                        "MISMATCH: Session count=%s vs pool count=%s (different connections?)",
                        count, pool_count,
                    )
            finally:
                release_connection(conn)
        except Exception as e:
            logger.warning("Could not run pool COUNT for comparison: %s", e)
    except Exception as e:
        logger.warning("Could not run final COUNT(*) on location_polygons: %s", e)
        stats["count_location_polygons_after"] = None
