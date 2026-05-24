#!/usr/bin/env python3
"""
Step 1 — Populate operations.field_locations from KML files only (no Google / reverse geocode).

- Parses polygon centroid (7 dp), extracted_village from KML metadata / placemark name.
- UNIQUE match: ABS(latitude - lat) < tolerance AND ABS(longitude - lon) < tolerance.
- If row exists → skip; else INSERT with village = extracted text or 'Unknown field'.

Run before crop_indices batch / pipeline reload:
  python scripts/ingest_field_locations_from_kml_dir.py --local-dir /path/to/kml
  python scripts/ingest_field_locations_from_kml_dir.py --local-dir /path/to/kml --limit 100
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass


def list_kmls(root: Path, limit: int | None) -> list[Path]:
    paths = sorted(root.rglob("*.kml"), key=lambda p: str(p).lower())
    out: list[Path] = []
    for p in paths:
        if not p.is_file():
            continue
        try:
            if p.stat().st_size == 0:
                continue
        except OSError:
            continue
        out.append(p)
        if limit is not None and len(out) >= limit:
            break
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest field_locations from KML directory (no API calls).")
    parser.add_argument("--local-dir", type=str, required=True, help="Directory containing .kml files (recursive)")
    parser.add_argument("--limit", type=int, default=None, help="Max files to process")
    args = parser.parse_args()

    root = Path(args.local_dir).resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    try:
        from core.db import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        db.execute(text("SELECT 1"))
    except Exception as e:
        print(f"DB unavailable: {e}", file=sys.stderr)
        return 1

    from crop_monitoring.kml_parser import parse_kml
    from crop_monitoring.database.location_repository import insert_field_location_if_absent
    from shapely.geometry import shape

    paths = list_kmls(root, args.limit)
    if not paths:
        print("No KML files found.")
        return 0

    inserted = 0
    skipped = 0
    errors = 0
    logger.info("Skipping Google API call (field_locations bulk ingest)")
    for i, path in enumerate(paths, 1):
        try:
            geojson, meta = parse_kml(path)
            geom = shape(geojson)
            c = geom.centroid
            lat_c = round(float(c.y), 7)
            lon_c = round(float(c.x), 7)
            extracted_village = meta.get("village_name") or meta.get("placemark_name")
            loc_id, was_inserted = insert_field_location_if_absent(
                db, lat_c, lon_c, extracted_village=extracted_village
            )
            if was_inserted:
                inserted += 1
                print(f"[{i}/{len(paths)}] New location inserted: {loc_id} ({path.name})")
            else:
                skipped += 1
                if loc_id:
                    print(f"[{i}/{len(paths)}] Skip (exists): {loc_id} ({path.name})")
                else:
                    errors += 1
                    print(f"[{i}/{len(paths)}] Failed: {path.name}", file=sys.stderr)
        except Exception as e:
            errors += 1
            logger.exception("Failed %s: %s", path.name, e)
            print(f"[{i}/{len(paths)}] Error {path.name}: {e}", file=sys.stderr)

    try:
        db.close()
    except Exception:
        pass

    print(f"Done. inserted={inserted}, skipped_existing={skipped}, errors={errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
