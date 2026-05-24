#!/usr/bin/env python3
"""
Backfill grower_id on sentinel{1,2,3}_indices and harvest_field_registry.

Uses operations.growers (reuse or create G_* ids). Run after alter_sentinel_schema_v3_grower.sql.

  python scripts/backfill_satellite_growers.py --report-dir reports/grower
  python scripts/backfill_satellite_growers.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TABLES = ("sentinel1_indices", "sentinel2_indices", "sentinel3_indices")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill grower_id on satellite index tables")
    parser.add_argument("--report-dir", type=str, default=str(_root / "reports" / "grower"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="Max distinct grower names to process")
    args = parser.parse_args()

    from sqlalchemy import text
    from core.db import SessionLocal
    from crop_monitoring.satellite_pipeline.grower_resolver import GrowerResolver
    from crop_monitoring.satellite_pipeline.grower_validation import write_grower_validation_reports
    from crop_monitoring.satellite_pipeline.harvest_field_mapping import MappingReport

    db = SessionLocal()
    resolver = GrowerResolver(db)
    name_to_gid: dict[str, str] = {}
    updated = {t: 0 for t in TABLES}

    try:
        rows = db.execute(
            text("""
                SELECT DISTINCT grower_name, location_id
                FROM (
                    SELECT grower_name, location_id FROM operations.sentinel1_indices
                    WHERE grower_name IS NOT NULL AND TRIM(grower_name) <> ''
                      AND (grower_id IS NULL OR TRIM(grower_id) = '')
                    UNION
                    SELECT grower_name, location_id FROM operations.sentinel2_indices
                    WHERE grower_name IS NOT NULL AND TRIM(grower_name) <> ''
                      AND (grower_id IS NULL OR TRIM(grower_id) = '')
                    UNION
                    SELECT grower_name, location_id FROM operations.sentinel3_indices
                    WHERE grower_name IS NOT NULL AND TRIM(grower_name) <> ''
                      AND (grower_id IS NULL OR TRIM(grower_id) = '')
                ) u
                ORDER BY grower_name
            """)
        ).fetchall()

        if args.limit:
            rows = rows[: args.limit]

        logger.info("Distinct grower names needing backfill: %d", len(rows))

        for grower_name, location_id in rows:
            norm = (grower_name or "").strip().lower()
            if norm in name_to_gid:
                gid = name_to_gid[norm]
            else:
                gid, canonical = resolver.resolve(grower_name, location_id=location_id)
                if not gid:
                    continue
                name_to_gid[norm] = gid
                grower_name = canonical

            if args.dry_run:
                logger.info("Would set %s → %s (%s)", grower_name, gid, location_id)
                continue

            for table in TABLES:
                r = db.execute(
                    text(f"""
                        UPDATE operations.{table}
                        SET grower_id = :gid,
                            grower_name = :gname,
                            updated_at = NOW()
                        WHERE grower_name IS NOT NULL
                          AND LOWER(REGEXP_REPLACE(TRIM(grower_name), '\\s+', ' ', 'g'))
                              = LOWER(REGEXP_REPLACE(TRIM(:gname), '\\s+', ' ', 'g'))
                          AND (grower_id IS NULL OR TRIM(grower_id) = '')
                    """),
                    {"gid": gid, "gname": grower_name},
                )
                updated[table] += r.rowcount or 0

        if not args.dry_run:
            db.execute(
                text("""
                    UPDATE operations.harvest_field_registry h
                    SET grower_id = g.grower_id,
                        updated_at = NOW()
                    FROM operations.growers g
                    WHERE h.grower_name IS NOT NULL
                      AND (h.grower_id IS NULL OR TRIM(h.grower_id) = '')
                      AND LOWER(REGEXP_REPLACE(TRIM(h.grower_name), '\\s+', ' ', 'g'))
                          = LOWER(REGEXP_REPLACE(TRIM(g.grower_name), '\\s+', ' ', 'g'))
                """)
            )
            db.commit()
            logger.info("Backfill row updates: %s", updated)
            logger.info("New growers created: %d", len(resolver.created_ids))
        else:
            db.rollback()

        report_dir = Path(args.report_dir).resolve()
        empty_report = MappingReport(created_grower_ids=list(resolver.created_ids))
        write_grower_validation_reports(db, empty_report, report_dir)
        if resolver.created_ids and not args.dry_run:
            from crop_monitoring.satellite_pipeline.grower_validation import export_new_grower_ids

            export_new_grower_ids(
                db,
                resolver.created_ids,
                report_dir / "grower_newly_created_grower_ids.csv",
            )
    finally:
        db.close()

    print(f"Done. Reports: {args.report_dir}")


if __name__ == "__main__":
    main()
