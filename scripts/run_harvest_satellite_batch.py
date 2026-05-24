#!/usr/bin/env python3
"""
Harvest-all-fields satellite v2 batch (253 KMLs).

- Maps internal_id from KML filename → grower/location via Excel/CSV (no Google API).
- Persists mappings to operations.harvest_field_registry.
- Runs v2 orchestrator with FieldContext (parallel to legacy crop_indices).

Usage:
  python scripts/run_harvest_satellite_batch.py --validate-only
  python scripts/run_harvest_satellite_batch.py --start 2025-12-01 --end 2026-03-18
  python scripts/run_harvest_satellite_batch.py --mode reprocess_raw --start ... --end ...
  python scripts/run_harvest_satellite_batch.py --limit 5 --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import date
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from config.satellite_paths import get_satellite_paths as _get_paths

_sp = _get_paths()
DEFAULT_KML_DIR = _sp.kml_dir
DEFAULT_DATA_ROOT = _sp.mapping_dir
DEFAULT_REPORT_DIR = _sp.report_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest KML batch — satellite v2")
    parser.add_argument("--kml-dir", type=str, default=str(DEFAULT_KML_DIR))
    parser.add_argument("--mapping-dir", type=str, default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--report-dir", type=str, default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--start", type=str, metavar="YYYY-MM-DD")
    parser.add_argument("--end", type=str, metavar="YYYY-MM-DD")
    parser.add_argument("--season-id", type=str, default="RABI_25_26")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only-internal-id", action="append", default=None)
    parser.add_argument("--maxcc", type=float, default=None)
    parser.add_argument(
        "--mode",
        choices=("incremental", "reprocess_raw"),
        default="incremental",
    )
    parser.add_argument("--batch-strategy", default="quarterly")
    parser.add_argument("--resume", action="store_true", default=True)
    parser.add_argument("--no-resume", action="store_false", dest="resume")
    parser.add_argument("--no-stac", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--skip-errors", action="store_true", help="Continue on per-file failures")
    args = parser.parse_args()

    kml_dir = Path(args.kml_dir).resolve()
    data_root = Path(args.mapping_dir).resolve()
    report_dir = Path(args.report_dir).resolve()

    from crop_monitoring.satellite_pipeline.harvest_field_mapping import (
        build_harvest_mappings,
        resolve_grower_ids_for_mappings,
    )
    from crop_monitoring.satellite_pipeline.harvest_validation import (
        run_full_validation,
        write_validation_reports,
    )

    from core.db import SessionLocal

    db_map = SessionLocal()
    report = build_harvest_mappings(
        kml_dir, data_root=data_root, season_id=args.season_id, db=db_map
    )
    try:
        report = resolve_grower_ids_for_mappings(db_map, report)
        db_map.commit()
    finally:
        db_map.close()
    if args.validate_only or not (args.limit or args.only_internal_id):
        report = run_full_validation(report, kml_dir)
        log_path, unmatched_path = write_validation_reports(report, report_dir)
    else:
        log_path = unmatched_path = report_dir / "(skipped-full-validation)"

    kml_count = len(list(kml_dir.glob("*.kml")))
    mapped_count = len(report.mappings)
    print(
        f"KML files on disk: {kml_count} | mapped: {mapped_count} | "
        f"errors: {len(report.errors)} | warnings: {len(report.warnings)}"
    )
    print(f"Validation log: {log_path}")
    print(f"Unmatched CSV: {unmatched_path}")

    if args.validate_only:
        from core.db import SessionLocal as _SL
        from crop_monitoring.satellite_pipeline.grower_validation import write_grower_validation_reports

        _db = _SL()
        try:
            write_grower_validation_reports(_db, report, report_dir / "grower")
            _db.commit()
        finally:
            _db.close()
        sys.exit(0 if not report.errors else 1)

    if not args.start or not args.end:
        print("--start and --end required for ingestion", file=sys.stderr)
        sys.exit(2)

    if args.dry_run:
        for iid in sorted(report.mappings.keys())[:20]:
            m = report.mappings[iid]
            print(f"  {m.internal_id} → {m.location_id} grower={m.grower_name}")
        sys.exit(0)

    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end)

    from core.db import SessionLocal
    from crop_monitoring.kml_parser import parse_kml
    from crop_monitoring.satellite_pipeline.field_context import FieldContext
    from crop_monitoring.satellite_pipeline.harvest_registry import persist_mapping_report
    from crop_monitoring.satellite_pipeline.orchestrator import SatelliteIngestionOrchestrator
    from crop_monitoring.database.sentinel_repositories import (
        create_ingestion_run,
        finish_ingestion_run,
    )
    from crop_monitoring.satellite_pipeline.cloud_config import S2CloudSettings
    from shapely.geometry import shape

    items = sorted(report.mappings.values(), key=lambda m: m.internal_id)
    if args.only_internal_id:
        allow = {x.strip().upper() for x in args.only_internal_id}
        items = [m for m in items if m.internal_id in allow]
    if args.limit:
        items = items[: args.limit]

    if report.errors and not args.skip_errors:
        print("Fix validation errors or pass --skip-errors", file=sys.stderr)
        sys.exit(1)

    run_id = uuid.UUID(args.run_id) if args.run_id else uuid.uuid4()
    db = SessionLocal()
    persist_mapping_report(db, report)
    db.commit()

    create_ingestion_run(
        db,
        run_id=str(run_id),
        mode=f"harvest_{args.mode}",
        season_id=args.season_id,
        time_start=start_d,
        time_end=end_d,
    )
    db.commit()

    cloud = S2CloudSettings.from_env(
        {"max_scene_cloud_pct": args.maxcc} if args.maxcc is not None else None
    )
    orch = SatelliteIngestionOrchestrator(
        db,
        season_id=args.season_id,
        start_date=start_d,
        end_date=end_d,
        cloud_settings=cloud,
        run_id=run_id,
        batch_strategy=args.batch_strategy,  # type: ignore[arg-type]
        resume=args.resume,
        use_stac=not args.no_stac,
    )

    ok = fail = 0
    for i, m in enumerate(items, 1):
        if m.validation_status == "error":
            logger.warning("Skipping invalid geometry %s", m.internal_id)
            fail += 1
            continue
        kml_path = Path(m.kml_path) if m.kml_path else kml_dir / m.file_name
        logger.info("[%s/%s] %s", i, len(items), m.file_name)
        try:
            geojson, _ = parse_kml(kml_path)
            ctx = FieldContext(
                internal_id=m.internal_id,
                location_id=m.location_id,
                file_name=m.file_name,
                season_id=m.season_id,
                grower_name=m.grower_name,
                grower_id=m.grower_id,
                legacy_file_name=m.legacy_file_name,
            )
            shape(geojson)  # validate
            counts = orch.process_kml_file(
                geojson=geojson,
                file_name=m.file_name,
                location_id=m.location_id,
                field=ctx,
                reprocess_only=(args.mode == "reprocess_raw"),
            )
            db.commit()
            logger.info("%s → %s", m.file_name, counts)
            if counts.get("errors", 0) > 0:
                fail += 1
            else:
                ok += 1
        except Exception as e:
            logger.exception("%s failed: %s", m.file_name, e)
            db.rollback()
            fail += 1
            if not args.skip_errors:
                break

    finish_ingestion_run(
        db,
        str(run_id),
        status="completed" if fail == 0 else "partial",
        files_processed=ok,
        files_failed=fail,
    )
    db.commit()

    from crop_monitoring.satellite_pipeline.grower_validation import (
        export_new_grower_ids,
        write_grower_validation_reports,
    )

    grower_report_dir = report_dir / "grower"
    write_grower_validation_reports(db, report, grower_report_dir)
    if report.created_grower_ids:
        export_new_grower_ids(
            db,
            report.created_grower_ids,
            grower_report_dir / "grower_newly_created_grower_ids.csv",
        )

    db.close()
    print(f"run_id={run_id} ok={ok} fail={fail}")
    print(f"Grower validation: {grower_report_dir}")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
