#!/usr/bin/env python3
"""
Batch run crop monitoring analysis for all KML files in a directory.
Results are stored in operations.crop_indices (do NOT use --no-db).

Requires: operations.crop_indices table with columns grower_name, variety_name
(village, town, district, state, country, postcode). Run:
  sql/alter_crop_indices_add_raw_metadata.sql
if needed.

Usage:
    python scripts/run_crop_analysis_batch.py
    python scripts/run_crop_analysis_batch.py --dir "C:\\path\\to\\kml\\folder"

Uses a single DB session; pipeline commits after each insert.
"""

import logging
import sys
from pathlib import Path

# Project root
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass

# Default directory with KML files (override with --dir)
DEFAULT_KML_DIR = Path(r"C:\Users\madan\Downloads\CG-20260302T122713Z-1-001\CG")

INDEX_ORDER = [
    "NDVI", "SAVI", "NDMI", "NDRE", "GCI", "PSRI", "MSAVI", "EVI", "LAI", "LST_C",
    "vv_db", "vh_db", "vh_vv_ratio",
]


def _fmt_val(v):
    if v is None:
        return "NaN"
    if isinstance(v, float):
        if v != v:  # NaN
            return "NaN"
        return f"{v:.4f}"
    return str(v)


def _safe_str(s):
    """Encode to ASCII with replacement so console (e.g. Windows charmap) does not raise UnicodeEncodeError."""
    if s is None:
        return "N/A"
    return (s if isinstance(s, str) else str(s)).encode("ascii", errors="replace").decode("ascii")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Batch crop analysis: all KML in directory -> DB")
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_KML_DIR,
        help="Directory containing .kml files",
    )
    args = parser.parse_args()

    kml_dir = args.dir.resolve()
    if not kml_dir.is_dir():
        print(f"Error: Directory not found: {kml_dir}", file=sys.stderr)
        sys.exit(1)

    kml_files = sorted(kml_dir.glob("*.kml"))
    total = len(kml_files)
    if total == 0:
        print(f"No .kml files in {kml_dir}")
        sys.exit(0)

    print(f"Found {total} KML file(s) in {kml_dir}\n")

    # Single DB session for the whole batch; pipeline commits after each insert
    db = None
    try:
        from core.db import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        db.execute(text("SELECT 1"))
    except Exception as e:
        print(f"Error: DB not available: {e}. Results cannot be saved.", file=sys.stderr)
        sys.exit(1)

    success_count = 0
    failed_count = 0
    failed_files = []

    for i, kml_path in enumerate(kml_files, start=1):
        print("---------------------------------")
        print(f"Processing file {i}/{total}")
        print(f"File: {kml_path.name}")
        print()

        try:
            from crop_monitoring.pipeline import run_crop_analysis

            result = run_crop_analysis(
                kml_path,
                start_date=None,
                end_date=None,
                store_in_db=True,
                db_session=db,
            )

            meta = result.get("metadata") or {}
            village = meta.get("village") or "N/A"
            grower = meta.get("grower") or "N/A"
            variety = meta.get("variety") or "N/A"

            print("Village:", _safe_str(village))
            print("Matched Village:", _safe_str(result.get("location_matched") or "-"))
            print("Location ID:", result.get("location_id") or "-")
            print()
            print("Grower:", _safe_str(grower))
            print("Matched Grower:", _safe_str(result.get("grower_matched") or "-"))
            print("Grower ID:", result.get("grower_id") or "-")
            print()
            print("Variety:", _safe_str(variety))
            print("Matched Variety:", _safe_str(result.get("variety_matched") or "-"))
            print("Variety ID:", result.get("variety_id") or "-")
            print()

            idx = result.get("indices") or {}
            print("Indices:")
            for k in INDEX_ORDER:
                v = idx.get(k)
                print(f"  {k}: {_fmt_val(v)}")
            print()
            print("Saved to operations.crop_indices")
            success_count += 1

        except Exception as e:
            import traceback
            logging.getLogger(__name__).exception("Pipeline failed for %s", kml_path.name)
            print(f"Failed: {e}")
            traceback.print_exc()
            failed_count += 1
            failed_files.append(kml_path.name)
        print()

    if db is not None:
        try:
            db.close()
        except Exception:
            pass

    print("---------------------------------")
    print("BATCH PROCESS SUMMARY")
    print("---------------------------------")
    print(f"Total files: {total}")
    print(f"Successfully processed: {success_count}")
    print(f"Failed: {failed_count}")
    if failed_files:
        print("Failed files:")
        for f in failed_files:
            print(f"  - {f}")
    print("---------------------------------")

    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()
