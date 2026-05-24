#!/usr/bin/env python3
"""
Copy rows from operations.crop_indices into operations.field_indices for given file_name(s).

Use this when:
  - Full batch cannot insert into crop_indices due to unique constraints on the same
    (location_id, date_start, variety_id, file_name) as existing rows, but you still
    want field_indices populated for testing; or
  - You need a quick mirror without re-calling Sentinel.

Examples:
  python scripts/backfill_field_indices_for_files.py --from-demo-kml 3
  python scripts/backfill_field_indices_for_files.py --file-name "foo.kml" --file-name "bar.kml"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill operations.field_indices from crop_indices.")
    parser.add_argument(
        "--from-demo-kml",
        type=int,
        metavar="N",
        default=None,
        help="Take first N KML basenames from demo_raw_test_kml (sorted) and copy matching crop_indices rows.",
    )
    parser.add_argument(
        "--file-name",
        action="append",
        dest="file_names",
        default=None,
        help="Normalized file_name as stored in crop_indices (repeatable).",
    )
    parser.add_argument(
        "--season-id",
        type=str,
        default=None,
        help="If set, only copy rows with this season_id.",
    )
    args = parser.parse_args()

    file_names: list[str] = []
    if args.from_demo_kml is not None:
        demo = _ROOT / "demo_raw_test_kml"
        paths = sorted(demo.rglob("*.kml"), key=lambda p: str(p).lower())[: max(0, int(args.from_demo_kml))]
        file_names = [p.name.replace("+", " ").strip() for p in paths]
    if args.file_names:
        file_names.extend([f.replace("+", " ").strip() for f in args.file_names])
    file_names = list(dict.fromkeys(file_names))
    if not file_names:
        print("Error: provide --from-demo-kml N and/or --file-name", file=sys.stderr)
        return 2

    from sqlalchemy import text

    from core.db import SessionLocal
    from crop_monitoring.database.repository import _insert_field_indices_row

    db = SessionLocal()
    n = 0
    try:
        params: dict = {"fns": file_names}
        season_clause = ""
        if args.season_id:
            season_clause = " AND season_id = :sid "
            params["sid"] = args.season_id.strip()

        q = text(
            f"""
            SELECT *
            FROM operations.crop_indices
            WHERE file_name = ANY(:fns)
            {season_clause}
            ORDER BY id
            """
        )
        rows = db.execute(q, params).mappings().all()
        if not rows:
            print(f"No crop_indices rows found for file_name in {file_names!r}" + (f" season={args.season_id!r}" if args.season_id else ""))
            return 1

        for r in rows:
            payload = {k: r[k] for k in r.keys()}
            payload["field_indices_batch_tag"] = payload.get("field_indices_batch_tag") or "backfill_from_crop_indices"
            rid = _insert_field_indices_row(db, payload)
            if rid > 0:
                n += 1
        db.commit()
        print(f"Inserted {n} row(s) into operations.field_indices from {len(rows)} crop_indices row(s).")
        return 0
    except Exception as e:
        db.rollback()
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
