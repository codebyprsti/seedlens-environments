#!/usr/bin/env python3
"""
Delete v2 satellite data for one or more fields (indices, raw, STAC, checkpoints).
Does NOT touch operations.crop_indices or operations.growers.

  python scripts/purge_satellite_field_data.py --internal-id IND-KA-600044
  python scripts/purge_satellite_field_data.py --location-id IND-KA-600044 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

from config.satellite_paths import get_satellite_paths  # noqa: E402

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Purge v2 satellite rows for field(s)")
    parser.add_argument("--internal-id", action="append", default=None)
    parser.add_argument("--location-id", action="append", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    locs: list[str] = []
    if args.internal_id:
        locs.extend(x.strip().upper() for x in args.internal_id)
    if args.location_id:
        locs.extend(x.strip().upper() for x in args.location_id)
    if not locs:
        print("Provide --internal-id or --location-id", file=sys.stderr)
        sys.exit(2)
    locs = list(dict.fromkeys(locs))

    from sqlalchemy import text
    from core.db import SessionLocal

    db = SessionLocal()
    counts: dict[str, int] = {}

    def _delete(table: str, sql: str, params: dict) -> None:
        if args.dry_run:
            row = db.execute(
                text(sql.replace("DELETE", "SELECT COUNT(*) FROM", 1).split("WHERE")[0]
                     + " WHERE " + sql.split("WHERE", 1)[1].split("RETURNING")[0].strip()),
                params,
            ).fetchone()
            # simpler count query per table below
            return
        r = db.execute(text(sql), params)
        counts[table] = counts.get(table, 0) + (r.rowcount or 0)

    try:
        for loc in locs:
            fn = f"{loc}.kml"
            params = {"loc": loc, "fn": fn}
            logger.info("Purging satellite v2 data for %s (%s)", loc, fn)

            for table in ("sentinel1_indices", "sentinel2_indices", "sentinel3_indices"):
                if args.dry_run:
                    n = db.execute(
                        text(f"SELECT COUNT(*) FROM operations.{table} WHERE location_id = :loc"),
                        {"loc": loc},
                    ).scalar()
                    counts[table] = counts.get(table, 0) + int(n or 0)
                else:
                    r = db.execute(
                        text(f"DELETE FROM operations.{table} WHERE location_id = :loc"),
                        {"loc": loc},
                    )
                    counts[table] = counts.get(table, 0) + (r.rowcount or 0)

            if args.dry_run:
                n = db.execute(
                    text("""
                        SELECT COUNT(*) FROM operations.satellite_raw_observation
                        WHERE location_id = :loc
                    """),
                    {"loc": loc},
                ).scalar()
                counts["satellite_raw_observation"] = counts.get("satellite_raw_observation", 0) + int(n or 0)
            else:
                r = db.execute(
                    text("DELETE FROM operations.satellite_raw_observation WHERE location_id = :loc"),
                    {"loc": loc},
                )
                counts["satellite_raw_observation"] = counts.get("satellite_raw_observation", 0) + (r.rowcount or 0)

            for table in ("stac_scene_catalog",):
                row = db.execute(
                    text("""
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'operations' AND table_name = :t LIMIT 1
                    """),
                    {"t": table},
                ).fetchone()
                if not row:
                    continue
                if args.dry_run:
                    n = db.execute(
                        text(f"SELECT COUNT(*) FROM operations.{table} WHERE location_id = :loc"),
                        {"loc": loc},
                    ).scalar()
                    counts[table] = counts.get(table, 0) + int(n or 0)
                else:
                    r = db.execute(
                        text(f"DELETE FROM operations.{table} WHERE location_id = :loc"),
                        {"loc": loc},
                    )
                    counts[table] = counts.get(table, 0) + (r.rowcount or 0)

            if args.dry_run:
                n = db.execute(
                    text("""
                        SELECT COUNT(*) FROM operations.satellite_ingestion_checkpoint
                        WHERE location_id = :loc OR file_name = :fn
                    """),
                    params,
                ).scalar()
                counts["satellite_ingestion_checkpoint"] = counts.get(
                    "satellite_ingestion_checkpoint", 0
                ) + int(n or 0)
            else:
                r = db.execute(
                    text("""
                        DELETE FROM operations.satellite_ingestion_checkpoint
                        WHERE location_id = :loc OR file_name = :fn
                    """),
                    params,
                )
                counts["satellite_ingestion_checkpoint"] = counts.get(
                    "satellite_ingestion_checkpoint", 0
                ) + (r.rowcount or 0)

        if args.dry_run:
            db.rollback()
            logger.info("Dry-run counts: %s", counts)
        else:
            db.commit()
            logger.info("Deleted rows: %s", counts)
    finally:
        db.close()

    print(counts)


if __name__ == "__main__":
    main()
