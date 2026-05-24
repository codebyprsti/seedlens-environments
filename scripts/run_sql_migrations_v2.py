#!/usr/bin/env python3
"""Apply v2 SQL migrations via DATABASE_URL (optional helper)."""

from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

MIGRATIONS = (
    "create_sentinel_indices_tables.sql",
    "create_stac_scene_catalog.sql",
    "alter_sentinel2_cloud_quality.sql",
    "alter_sentinel_indices_harvest_fields.sql",
    "alter_sentinel_schema_v3_grower.sql",
)


def main() -> None:
    from sqlalchemy import text
    from core.db import SessionLocal

    db = SessionLocal()
    sql_dir = _root / "sql"
    for name in MIGRATIONS:
        path = sql_dir / name
        if not path.is_file():
            print(f"SKIP missing {name}")
            continue
        sql = path.read_text(encoding="utf-8")
        print(f"Applying {name}...")
        try:
            db.execute(text(sql))
            db.commit()
            print(f"  OK {name}")
        except Exception as e:
            db.rollback()
            print(f"  FAIL {name}: {e}")
    db.close()
    print("Done. Run backfill separately: sql/backfill_crop_indices_to_sentinel_tables.sql")


if __name__ == "__main__":
    main()
