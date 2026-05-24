#!/usr/bin/env python3
"""
Satellite v2 pipeline — sentinel1/2/3_indices + raw + STAC + checkpoints.

Modes:
  incremental     — API fetch + store (default)
  reprocess_raw   — rebuild indices from satellite_raw_observation only
  backfill_sql    — print SQL migration commands only

Legacy crop_indices pipeline is NOT modified.

Usage:
  python scripts/run_satellite_ingestion_v2.py --local-dir ./kml --start 2025-12-01 --end 2026-03-18
  python scripts/run_satellite_ingestion_v2.py --mode reprocess_raw --local-dir ./kml --start ... --end ...
  python scripts/run_satellite_ingestion_v2.py --batch-strategy monthly --resume
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


def _run_migrations_hint() -> None:
    sql_dir = _root / "sql"
    print("Run migrations in order:")
    for name in (
        "create_sentinel_indices_tables.sql",
        "create_stac_scene_catalog.sql",
        "alter_sentinel_indices_harvest_fields.sql",
        "backfill_view_dynamic.sql",
        "backfill_crop_indices_to_sentinel_tables.sql",
    ):
        p = sql_dir / name
        if p.is_file():
            print(f"  psql $DATABASE_URL -f {p}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Satellite v2 ingestion pipeline")
    parser.add_argument("--local-dir", type=str, help="KML directory (recursive)")
    parser.add_argument("--start", type=str, metavar="YYYY-MM-DD")
    parser.add_argument("--end", type=str, metavar="YYYY-MM-DD")
    parser.add_argument("--season-id", type=str, default="RABI_25_26")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--maxcc",
        type=float,
        default=None,
        help="S2 scene cloud ceiling %% (default 60 or S2_MAX_CLOUD_COVER env)",
    )
    parser.add_argument(
        "--mode",
        choices=("incremental", "reprocess_raw", "migrate_hint"),
        default="incremental",
    )
    parser.add_argument(
        "--batch-strategy",
        choices=("weekly", "monthly", "quarterly", "full"),
        default="quarterly",
        help="Temporal chunking for Statistical API",
    )
    parser.add_argument("--resume", action="store_true", default=True)
    parser.add_argument("--no-resume", action="store_false", dest="resume")
    parser.add_argument("--no-stac", action="store_true", help="Skip STAC catalogue fetch")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only-file", action="append", default=None)
    parser.add_argument("--run-id", type=str, default=None, help="Resume existing run UUID")
    args = parser.parse_args()

    if args.mode == "migrate_hint":
        _run_migrations_hint()
        sys.exit(0)

    if not args.local_dir or not args.start or not args.end:
        print("--local-dir, --start, --end required", file=sys.stderr)
        sys.exit(2)

    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end)
    kml_dir = Path(args.local_dir).resolve()

    from scripts.run_crop_analysis_s3_batch import list_kml_from_local

    paths = list_kml_from_local(kml_dir)
    if args.only_file:
        allow = {n.strip().lower() for n in args.only_file}
        paths = [p for p in paths if p.name.lower() in allow]
    if args.limit:
        paths = paths[: args.limit]

    print(f"Mode={args.mode} KMLs={len(paths)} strategy={args.batch_strategy}")
    if args.dry_run:
        for p in paths[:15]:
            print(f"  {p.name}")
        sys.exit(0)

    from core.db import SessionLocal
    from crop_monitoring.database.location_repository import get_field_location_row_by_centroid
    from crop_monitoring.database.sentinel_repositories import create_ingestion_run, finish_ingestion_run
    from crop_monitoring.kml_parser import parse_kml
    from crop_monitoring.satellite_pipeline.orchestrator import SatelliteIngestionOrchestrator
    from shapely.geometry import shape

    run_id = uuid.UUID(args.run_id) if args.run_id else uuid.uuid4()
    db = SessionLocal()
    mode_label = "reprocess_raw" if args.mode == "reprocess_raw" else "incremental"
    create_ingestion_run(
        db,
        run_id=str(run_id),
        mode=mode_label,
        season_id=args.season_id,
        time_start=start_d,
        time_end=end_d,
    )
    db.commit()

    from crop_monitoring.satellite_pipeline.cloud_config import S2CloudSettings

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
    for i, kml_path in enumerate(paths, 1):
        fn = kml_path.name
        logger.info("[%s/%s] %s", i, len(paths), fn)
        try:
            geojson, _ = parse_kml(kml_path)
            c = shape(geojson).centroid
            fl = get_field_location_row_by_centroid(db, round(float(c.y), 7), round(float(c.x), 7))
            if not fl:
                logger.warning("No field_locations for %s", fn)
                fail += 1
                continue
            counts = orch.process_kml_file(
                geojson=geojson,
                file_name=fn,
                location_id=str(fl["location_id"]),
                reprocess_only=(args.mode == "reprocess_raw"),
            )
            db.commit()
            logger.info("%s → %s", fn, counts)
            if counts.get("errors", 0) > 0:
                fail += 1
            else:
                ok += 1
        except Exception as e:
            logger.exception("%s failed: %s", fn, e)
            db.rollback()
            fail += 1

    finish_ingestion_run(
        db,
        str(run_id),
        status="completed" if fail == 0 else "partial",
        files_processed=ok,
        files_failed=fail,
    )
    db.commit()
    db.close()
    print(f"run_id={run_id} ok={ok} fail={fail}")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
