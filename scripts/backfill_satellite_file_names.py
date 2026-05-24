#!/usr/bin/env python3
"""
Set canonical file_name on satellite tables from harvest manifest / crop_indices.

On-disk KML may be renamed to {internal_id}.kml; DB file_name must match crop_indices.

  python scripts/backfill_satellite_file_names.py
  python scripts/backfill_satellite_file_names.py --internal-id IND-KA-600044 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:
    pass

from config.satellite_paths import get_satellite_paths as _sp

_paths = _sp()
DEFAULT_KML_DIR = _paths.kml_dir
DEFAULT_DATA_ROOT = _paths.mapping_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill canonical file_name on satellite rows")
    parser.add_argument("--kml-dir", type=str, default=str(DEFAULT_KML_DIR))
    parser.add_argument("--mapping-dir", type=str, default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--internal-id", action="append", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy import text
    from core.db import SessionLocal
    from crop_monitoring.satellite_pipeline.harvest_field_mapping import build_harvest_mappings

    db = SessionLocal()
    report = build_harvest_mappings(
        Path(args.kml_dir), data_root=Path(args.mapping_dir), db=db
    )
    items = list(report.mappings.values())
    if args.internal_id:
        allow = {x.strip().upper() for x in args.internal_id}
        items = [m for m in items if m.internal_id in allow]

    counts: dict[str, int] = {}
    tables = (
        "sentinel1_indices",
        "sentinel2_indices",
        "sentinel3_indices",
        "satellite_raw_observation",
        "stac_scene_catalog",
        "harvest_field_registry",
        "satellite_ingestion_checkpoint",
    )

    try:
        for m in items:
            if m.file_name == m.legacy_file_name:
                continue
            params = {
                "loc": m.location_id,
                "fn": m.file_name,
                "disk": m.legacy_file_name,
            }
            for table in tables:
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
                        text(f"""
                            SELECT COUNT(*) FROM operations.{table}
                            WHERE location_id = :loc
                              AND (file_name = :disk OR file_name IS NULL)
                        """),
                        params,
                    ).scalar()
                    counts[table] = counts.get(table, 0) + int(n or 0)
                else:
                    if table == "harvest_field_registry":
                        r = db.execute(
                            text("""
                                UPDATE operations.harvest_field_registry
                                SET file_name = :fn,
                                    legacy_file_name = :disk,
                                    updated_at = NOW()
                                WHERE internal_id = :loc
                            """),
                            params,
                        )
                    elif table == "satellite_ingestion_checkpoint":
                        r = db.execute(
                            text("""
                                UPDATE operations.satellite_ingestion_checkpoint
                                SET file_name = :fn, updated_at = NOW()
                                WHERE location_id = :loc
                                  AND (file_name = :disk OR file_name IS NULL)
                            """),
                            params,
                        )
                    elif table in (
                        "sentinel1_indices",
                        "sentinel2_indices",
                        "sentinel3_indices",
                        "harvest_field_registry",
                    ):
                        r = db.execute(
                            text(f"""
                                UPDATE operations.{table}
                                SET file_name = :fn, updated_at = NOW()
                                WHERE location_id = :loc
                                  AND (file_name = :disk OR file_name IS NULL)
                            """),
                            params,
                        )
                    else:
                        r = db.execute(
                            text(f"""
                                UPDATE operations.{table}
                                SET file_name = :fn
                                WHERE location_id = :loc
                                  AND (file_name = :disk OR file_name IS NULL)
                            """),
                            params,
                        )
                    counts[table] = counts.get(table, 0) + (r.rowcount or 0)

        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    finally:
        db.close()

    print(f"Updated rows: {counts}")


if __name__ == "__main__":
    main()
