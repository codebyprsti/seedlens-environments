#!/usr/bin/env python3
"""
Remove duplicate dev_crop_intel.crop_indices rows by observation_date (index date).

For each (file_name, location_id, season_id, observation_date), keeps one row:
  1. Prefer date_start = observation_date (aligned with operations.index_date)
  2. Else highest id (newest)

Default file list: the 4 Odisha files that had date_start-offset duplicates.

Example:
  python scripts/dedupe_dev_crop_intel_index_date.py --season-id RABI_25_26
  python scripts/dedupe_dev_crop_intel_index_date.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Files with observation_date duplicates (old date_start offset + new sync)
DEFAULT_FILES = [
    "Bahabila Nandkishore Dali Usrh24 Dala  Dali.kml",
    "Bahabila Prasanta Behera Usrh24 Fakir Mohan Behera.kml",
    "KUSUDA USRH24 NIGAMANANDA DAS RADHASHYAM DAS 1.kml",
    "Sahapur Harisankar Das Usrh24 Shasashsr Das.kml",
]

_DEDUPE_DELETE_SQL = """
DELETE FROM dev_crop_intel.crop_indices
WHERE id IN (
    SELECT id
    FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY file_name, location_id, season_id, observation_date
                   ORDER BY
                       CASE WHEN date_start IS NOT DISTINCT FROM observation_date THEN 0 ELSE 1 END,
                       id DESC
               ) AS rn
        FROM dev_crop_intel.crop_indices
        WHERE file_name = ANY(:fns)
          AND (:sid IS NULL OR season_id = :sid)
          AND observation_date IS NOT NULL
    ) ranked
    WHERE rn > 1
)
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dedupe dev_crop_intel.crop_indices by observation_date (index date)."
    )
    parser.add_argument("--file-name", action="append", dest="file_names", default=None)
    parser.add_argument("--season-id", type=str, default="RABI_25_26")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    file_names: list[str] = []
    if not args.file_names:
        file_names.extend(DEFAULT_FILES)
    if args.file_names:
        file_names.extend(f.replace("+", " ").strip() for f in args.file_names)
    file_names = list(dict.fromkeys(file_names))
    if not file_names:
        print("Provide --file-name or use default four Odisha files.", file=sys.stderr)
        return 2

    from sqlalchemy import text

    from core.db import SessionLocal

    season_id = (args.season_id or "").strip() or None
    db = SessionLocal()
    try:
        print("Files:")
        for fn in file_names:
            print(f"  {fn}")

        before_rows = db.execute(
            text(
                """
                SELECT file_name, COUNT(*) AS n
                FROM dev_crop_intel.crop_indices
                WHERE file_name = ANY(:fns)
                  AND (:sid IS NULL OR season_id = :sid)
                GROUP BY file_name
                ORDER BY file_name
                """
            ),
            {"fns": file_names, "sid": season_id},
        ).fetchall()
        total_before = sum(n for _, n in before_rows)
        print(f"\nBefore ({total_before} rows):")
        for fn, n in before_rows:
            print(f"  {n:4d}  {fn}")

        dup_groups = db.execute(
            text(
                """
                SELECT file_name, observation_date, COUNT(*) AS c
                FROM dev_crop_intel.crop_indices
                WHERE file_name = ANY(:fns)
                  AND (:sid IS NULL OR season_id = :sid)
                  AND observation_date IS NOT NULL
                GROUP BY file_name, location_id, season_id, observation_date
                HAVING COUNT(*) > 1
                """
            ),
            {"fns": file_names, "sid": season_id},
        ).fetchall()
        to_delete = db.execute(
            text(
                """
                SELECT COUNT(*) FROM (
                    SELECT id
                    FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY file_name, location_id, season_id, observation_date
                                   ORDER BY
                                       CASE WHEN date_start IS NOT DISTINCT FROM observation_date THEN 0 ELSE 1 END,
                                       id DESC
                               ) AS rn
                        FROM dev_crop_intel.crop_indices
                        WHERE file_name = ANY(:fns)
                          AND (:sid IS NULL OR season_id = :sid)
                          AND observation_date IS NOT NULL
                    ) ranked
                    WHERE rn > 1
                ) del_ids
                """
            ),
            {"fns": file_names, "sid": season_id},
        ).scalar()
        print(f"\nDuplicate index-date groups: {len(dup_groups)}")
        print(f"Rows to delete: {to_delete}")

        if args.dry_run:
            return 0

        if to_delete == 0:
            print("\nNothing to delete.")
            return 0

        db.execute(text(_DEDUPE_DELETE_SQL), {"fns": file_names, "sid": season_id})
        db.commit()

        after_rows = db.execute(
            text(
                """
                SELECT file_name, COUNT(*) AS n
                FROM dev_crop_intel.crop_indices
                WHERE file_name = ANY(:fns)
                  AND (:sid IS NULL OR season_id = :sid)
                GROUP BY file_name
                ORDER BY file_name
                """
            ),
            {"fns": file_names, "sid": season_id},
        ).fetchall()
        total_after = sum(n for _, n in after_rows)
        print(f"\nAfter ({total_after} rows, removed {total_before - total_after}):")
        for fn, n in after_rows:
            print(f"  {n:4d}  {fn}")

        return 0
    except Exception as e:
        db.rollback()
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
