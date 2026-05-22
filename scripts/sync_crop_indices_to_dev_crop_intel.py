#!/usr/bin/env python3
"""
Copy or upsert rows from operations.crop_indices → dev_crop_intel.crop_indices.

Maps operations.index_date → dev observation_date / date_start / date_end,
and extracted_grower → grower_name.

Example:
  python scripts/sync_crop_indices_to_dev_crop_intel.py --season-id RABI_25_26 --missing-kmls-batch
  python scripts/sync_crop_indices_to_dev_crop_intel.py --file-name "foo.kml" --season-id RABI_25_26
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# Processed in operations (missing_kmls batch); excludes invalid KOTIHAL KML
MISSING_KMLS_BATCH = [
    "ARELAKAMAPUR SUNIL H KARENNANAVAR USRH24 HANUMANTHAPPA.kml",
    "ARELAKAMAPUR TAVANAPPA T KADUR USRH24 TUKKAPPA.kml",
    "Kuruthia Bimala Parida Usrh24 Anil Parida 1.kml",
    "Kuruthia Bimala Parida Usrh24 Anil Parida 2.kml",
    "Bahabila Nandkishore Dali Usrh24 Dala  Dali.kml",
    "Bahabila Prasanta Behera Usrh24 Fakir Mohan Behera.kml",
    "KUSUDA USRH24 NIGAMANANDA DAS RADHASHYAM DAS 1.kml",
    "Sahapur Harisankar Das Usrh24 Shasashsr Das.kml",
]

METRIC_COLS = [
    "ndvi",
    "savi",
    "ndmi",
    "ndre",
    "gci",
    "psri",
    "msavi",
    "evi",
    "lai",
    "lst_celsius",
    "vv",
    "vh",
    "vh_vv_ratio",
    "crop_id",
    "area_acre",
    "distance_km",
    "season_id",
    "ndwi",
    "ndwi_gao",
    "blue",
    "green",
    "red",
    "rededge1",
    "rededge2",
    "rededge3",
    "nir",
    "narrow_nir",
    "swir1",
    "swir2",
]


def _upsert_sql() -> str:
    metric_sets = ",\n  ".join(f"{c} = EXCLUDED.{c}" for c in METRIC_COLS)
    metric_cols = ", ".join(METRIC_COLS)
    metric_select = ", ".join(f"o.{c}" for c in METRIC_COLS)
    return f"""
INSERT INTO dev_crop_intel.crop_indices (
  location_id,
  grower_id,
  variety_id,
  date_start,
  date_end,
  observation_date,
  {metric_cols},
  file_name,
  grower_name
)
SELECT
  o.location_id,
  o.grower_id,
  o.variety_id,
  o.index_date AS date_start,
  o.index_date AS date_end,
  o.index_date AS observation_date,
  {metric_select},
  o.file_name,
  o.extracted_grower AS grower_name
FROM operations.crop_indices o
WHERE o.file_name = ANY(:fns)
  AND (:sid IS NULL OR o.season_id = :sid)
ON CONFLICT (location_id, date_start, variety_id, file_name)
DO UPDATE SET
  date_end = EXCLUDED.date_end,
  observation_date = EXCLUDED.observation_date,
  grower_id = EXCLUDED.grower_id,
  grower_name = EXCLUDED.grower_name,
  {metric_sets}
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upsert operations.crop_indices rows into dev_crop_intel.crop_indices."
    )
    parser.add_argument("--file-name", action="append", dest="file_names", default=None)
    parser.add_argument(
        "--missing-kmls-batch",
        action="store_true",
        help="Sync the 8 processed missing_kmls file names (excludes empty KOTIHAL KML).",
    )
    parser.add_argument("--season-id", type=str, default="RABI_25_26")
    parser.add_argument("--dry-run", action="store_true", help="Count source rows only; no write.")
    args = parser.parse_args()

    file_names: list[str] = []
    if args.missing_kmls_batch:
        file_names.extend(MISSING_KMLS_BATCH)
    if args.file_names:
        file_names.extend(f.replace("+", " ").strip() for f in args.file_names)
    file_names = list(dict.fromkeys(file_names))
    if not file_names:
        print("Provide --file-name and/or --missing-kmls-batch", file=sys.stderr)
        return 2

    from sqlalchemy import text

    from core.db import SessionLocal

    season_id = (args.season_id or "").strip() or None
    db = SessionLocal()
    try:
        src = db.execute(
            text(
                """
                SELECT file_name, COUNT(*) AS n
                FROM operations.crop_indices
                WHERE file_name = ANY(:fns)
                  AND (:sid IS NULL OR season_id = :sid)
                GROUP BY file_name
                ORDER BY file_name
                """
            ),
            {"fns": file_names, "sid": season_id},
        ).fetchall()
        if not src:
            print("No source rows in operations.crop_indices for given file_name(s).")
            return 1

        print("Source (operations.crop_indices):")
        total_src = 0
        for fn, n in src:
            print(f"  {n:4d}  {fn}")
            total_src += n
        print(f"  Total: {total_src}")

        if args.dry_run:
            return 0

        before = db.execute(
            text(
                """
                SELECT COUNT(*) FROM dev_crop_intel.crop_indices
                WHERE file_name = ANY(:fns)
                  AND (:sid IS NULL OR season_id = :sid)
                """
            ),
            {"fns": file_names, "sid": season_id},
        ).scalar()

        db.execute(text(_upsert_sql()), {"fns": file_names, "sid": season_id})
        db.commit()

        after = db.execute(
            text(
                """
                SELECT COUNT(*) FROM dev_crop_intel.crop_indices
                WHERE file_name = ANY(:fns)
                  AND (:sid IS NULL OR season_id = :sid)
                """
            ),
            {"fns": file_names, "sid": season_id},
        ).scalar()

        print(f"\ndev_crop_intel.crop_indices: {before} -> {after} rows (upserted {total_src} from operations)")
        return 0
    except Exception as e:
        db.rollback()
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
