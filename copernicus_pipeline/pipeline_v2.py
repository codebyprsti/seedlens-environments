"""V2 satellite pipeline entry (does not modify crop_indices)."""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from copernicus_pipeline.db import assert_database_configured, assert_sentinel_configured
from copernicus_pipeline.errors import ValidationError
from copernicus_pipeline.runner import list_kml_files, normalize_basename, validate_iso_date

logger = logging.getLogger(__name__)


def run_pipeline_v2(
    *,
    kml_dir: Path,
    start: str,
    end: str,
    season_id: Optional[str] = None,
    limit: Optional[int] = None,
    only_files: Optional[Sequence[str]] = None,
    dry_run: bool = False,
    maxcc: float = 60.0,
    mode: str = "incremental",
    batch_strategy: str = "quarterly",
    maxcc: float = 60.0,
    resume: bool = True,
    use_stac: bool = True,
    skip_env_checks: bool = False,
) -> int:
    start_d = date.fromisoformat(validate_iso_date("--start", start))
    end_d = date.fromisoformat(validate_iso_date("--end", end))
    if limit is not None and limit < 0:
        raise ValidationError("--limit must be non-negative")

    if not skip_env_checks and not dry_run:
        assert_database_configured()
        if mode != "reprocess_raw":
            assert_sentinel_configured()

    paths = list_kml_files(Path(kml_dir))
    if only_files:
        allow = {normalize_basename(n) for n in only_files if n}
        paths = [p for p in paths if normalize_basename(p.name) in allow]
    if limit is not None:
        paths = paths[:limit]

    if dry_run:
        for p in paths[:30]:
            print(p.name)
        return 0

    from core.db import SessionLocal
    from crop_monitoring.database.location_repository import get_field_location_row_by_centroid
    from crop_monitoring.database.sentinel_repositories import create_ingestion_run, finish_ingestion_run
    from crop_monitoring.kml_parser import parse_kml
    from crop_monitoring.satellite_pipeline.orchestrator import SatelliteIngestionOrchestrator
    from shapely.geometry import shape

    run_id = uuid.uuid4()
    db = SessionLocal()
    sid = (season_id or "").strip() or "RABI_25_26"
    create_ingestion_run(
        db,
        run_id=str(run_id),
        mode=mode,
        season_id=sid,
        time_start=start_d,
        time_end=end_d,
    )
    db.commit()

    orch = SatelliteIngestionOrchestrator(
        db,
        season_id=sid,
        start_date=start_d,
        end_date=end_d,
        max_cloud_cover=float(maxcc),
        run_id=run_id,
        batch_strategy=batch_strategy,  # type: ignore[arg-type]
        resume=resume,
        use_stac=use_stac,
    )

    ok = fail = 0
    reprocess_only = mode == "reprocess_raw"
    for p in paths:
        try:
            geojson, _ = parse_kml(p)
            c = shape(geojson).centroid
            fl = get_field_location_row_by_centroid(db, round(float(c.y), 7), round(float(c.x), 7))
            if not fl:
                fail += 1
                continue
            counts = orch.process_kml_file(
                geojson=geojson,
                file_name=p.name,
                location_id=str(fl["location_id"]),
                reprocess_only=reprocess_only,
            )
            db.commit()
            if counts.get("errors", 0) > 0:
                fail += 1
            else:
                ok += 1
        except Exception as e:
            logger.exception("v2 failed %s: %s", p.name, e)
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
    logger.info("v2 run_id=%s ok=%s fail=%s", run_id, ok, fail)
    return 0 if fail == 0 else 1
