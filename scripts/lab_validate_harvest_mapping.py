#!/usr/bin/env python3
"""
Validate harvest KML internal_id → canonical file_name mapping before lab upload.

Example (local source):
  python scripts/lab_validate_harvest_mapping.py \\
    --kml-dir "C:/Users/madan/Downloads/.../harvest_all_fields" \\
    --mapping-dir "C:/Users/madan/Downloads/.../harvest_all_fields" \\
    --only-internal-id IND-KA-600044

Example (lab):
  python scripts/lab_validate_harvest_mapping.py \\
    --kml-dir /home/madanm/kml_files \\
    --mapping-dir /home/madanm/kml_files \\
    --resolve-growers
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate harvest filename mapping.")
    parser.add_argument("--kml-dir", type=Path, required=True)
    parser.add_argument("--mapping-dir", type=Path, default=None, help="Excel/CSV/manifest (default: kml-dir)")
    parser.add_argument("--season-id", type=str, default="RABI_25_26")
    parser.add_argument("--only-internal-id", action="append", default=None)
    parser.add_argument("--resolve-growers", action="store_true", help="Resolve grower_id via DB")
    parser.add_argument("--report-dir", type=Path, default=_ROOT / "reports" / "harvest")
    parser.add_argument("--log-level", type=str, default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    kml_dir = args.kml_dir.expanduser().resolve()
    mapping_dir = (args.mapping_dir or kml_dir).expanduser().resolve()

    if not kml_dir.is_dir():
        print(f"KML dir not found: {kml_dir}", file=sys.stderr)
        return 2

    kml_count = len(list(kml_dir.glob("*.kml")))
    print(f"KML dir: {kml_dir} ({kml_count} .kml files)")
    print(f"Mapping dir: {mapping_dir}")

    from crop_monitoring.satellite_pipeline.harvest_field_mapping import (
        build_harvest_mappings,
        resolve_grower_ids_for_mappings,
    )
    from crop_monitoring.satellite_pipeline.harvest_validation import (
        run_full_validation,
        write_validation_reports,
    )

    db = None
    if args.resolve_growers:
        from core.db import SessionLocal

        db = SessionLocal()

    try:
        report = build_harvest_mappings(
            kml_dir,
            data_root=mapping_dir,
            season_id=args.season_id,
            db=db,
        )
        if args.resolve_growers and db is not None:
            report = resolve_grower_ids_for_mappings(db, report)
            db.commit()

        report = run_full_validation(report, kml_dir)
        log_p, csv_p = write_validation_reports(report, args.report_dir)

        allow = None
        if args.only_internal_id:
            allow = {x.strip().upper() for x in args.only_internal_id}

        print("\n--- Mapping summary ---")
        print(f"errors={len(report.errors)} warnings={len(report.warnings)}")
        shown = 0
        for iid in sorted(report.mappings.keys()):
            if allow and iid not in allow:
                continue
            m = report.mappings[iid]
            fn_ok = m.file_name != m.legacy_file_name
            print(
                f"{m.internal_id} | disk={m.legacy_file_name} | "
                f"file_name={m.file_name} | fn_from_manifest={'Y' if fn_ok else 'FALLBACK'} | "
                f"grower={m.grower_name or 'NULL'} | grower_id={m.grower_id or 'NULL'} | "
                f"status={m.validation_status}"
            )
            shown += 1
            if allow and shown >= len(allow):
                break

        if allow:
            missing = allow - set(report.mappings.keys())
            if missing:
                print(f"\nUNMATCHED internal_ids (no KML on disk): {sorted(missing)}")

        print(f"\nReports: {log_p}\n         {csv_p}")
        return 0 if not report.errors else 1
    except Exception as e:
        if db is not None:
            db.rollback()
        raise e
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
