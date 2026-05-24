#!/usr/bin/env python3
"""
Apply crop_indices-aligned column names + S1 linear backscatter fix on sentinel tables.

  python scripts/migrate_sentinel_columns_to_crop_indices.py --dry-run
  python scripts/migrate_sentinel_columns_to_crop_indices.py --apply
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

from sqlalchemy import text

from core.db import SessionLocal
from crop_monitoring.satellite_pipeline.crop_indices_columns import (
    S2_BAND_TO_CROP,
    normalize_s1_scatter_fields,
)


def _s2_sample(db, limit: int = 3):
    cols = sorted({v for k, v in S2_BAND_TO_CROP.items() if k == k.lower()})
    old_cols = [k for k in S2_BAND_TO_CROP if k == k.lower()]
    # pick columns that exist
    existing = {
        r[0]
        for r in db.execute(
            text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'operations' AND table_name = 'sentinel2_indices'
            """)
        ).fetchall()
    }
    show = [c for c in cols if c in existing][:5]
    if not show:
        show = [c for c in old_cols if c in existing][:5]
    if not show:
        return []
    sel = ", ".join(show)
    return db.execute(
        text(f"SELECT acquisition_date, {sel} FROM operations.sentinel2_indices LIMIT :n"),
        {"n": limit},
    ).fetchall()


def _s1_stats(db):
    row = db.execute(
        text("""
            SELECT
              COUNT(*) AS n,
              COUNT(*) FILTER (WHERE vv IS NOT NULL AND vv > 0 AND vv <= 1) AS vv_linear,
              COUNT(*) FILTER (WHERE vv IS NOT NULL AND vv < 0) AS vv_looks_db,
              COUNT(*) FILTER (WHERE vv_db IS NOT NULL) AS has_vv_db
            FROM operations.sentinel1_indices
        """)
    ).fetchone()
    return dict(row._mapping) if row else {}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Report only (default)")
    p.add_argument("--apply", action="store_true", help="Execute SQL migration")
    args = p.parse_args()
    apply = args.apply

    sql_path = _root / "sql" / "alter_sentinel_crop_indices_column_names.sql"
    sql = sql_path.read_text(encoding="utf-8")

    db = SessionLocal()
    try:
        print("=== BEFORE ===")
        print("S1 stats:", _s1_stats(db))
        print("S2 sample:", _s2_sample(db))

        if apply:
            db.execute(text(sql))
            db.commit()
            print("\nApplied:", sql_path.name)
        else:
            print("\nDRY RUN — use --apply to execute SQL migration")

        print("\n=== AFTER ===")
        print("S1 stats:", _s1_stats(db))
        print("S2 sample:", _s2_sample(db))

        # Python-side verify on a few S1 rows
        rows = db.execute(
            text("""
                SELECT vv, vh, vv_db, vh_db, vh_vv_ratio
                FROM operations.sentinel1_indices
                WHERE vv IS NOT NULL OR vv_db IS NOT NULL
                LIMIT 5
            """)
        ).fetchall()
        for r in rows:
            norm = normalize_s1_scatter_fields(dict(r._mapping))
            print("S1 normalized sample:", norm)
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
