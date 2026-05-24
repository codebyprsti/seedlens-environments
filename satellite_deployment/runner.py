"""
Production harvest batch runner — resumable, logged, rate-limited.

Wraps existing v2 orchestrator; does not modify legacy crop_indices pipeline.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional

from satellite_deployment.health import HealthSnapshot

logger = logging.getLogger("satellite.ingestion")
fail_logger = logging.getLogger("satellite.failed")
retry_logger = logging.getLogger("satellite.retry")
perf_logger = logging.getLogger("satellite.performance")
api_logger = logging.getLogger("satellite.api")


@dataclass
class RunConfig:
    start: date
    end: date
    season_id: str = "RABI_25_26"
    mode: str = "incremental"  # incremental | reprocess_raw
    batch_strategy: str = "quarterly"
    resume: bool = True
    use_stac: bool = True
    s1_s3_on_s2_days_only: bool = False
    max_cloud_cover: Optional[float] = None
    api_delay_seconds: float = 2.0
    max_retries_per_field: int = 2
    retry_backoff_seconds: float = 30.0
    skip_errors: bool = True
    limit: Optional[int] = None
    only_internal_ids: Optional[list[str]] = None
    run_id: Optional[uuid.UUID] = None
    validate_only: bool = False
    dry_run: bool = False
    continue_last_run: bool = False


class HarvestDeploymentRunner:
    def __init__(
        self,
        *,
        paths,
        kml_dir: Optional[Path] = None,
        mapping_dir: Optional[Path] = None,
        report_dir: Optional[Path] = None,
    ) -> None:
        self.paths = paths
        self.kml_dir = kml_dir or paths.kml_dir
        self.mapping_dir = mapping_dir or paths.mapping_dir
        self.report_dir = report_dir or paths.report_dir
        self.checkpoint_dir = paths.checkpoint_dir
        self.health_path = self.checkpoint_dir / "health.json"
        self.state_path = self.checkpoint_dir / "run_state.json"
        self.failed_list_path = self.checkpoint_dir / "failed_internal_ids.txt"

    def _load_failed_ids(self) -> set[str]:
        if not self.failed_list_path.is_file():
            return set()
        return {
            line.strip().upper()
            for line in self.failed_list_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    def _append_failed_id(self, internal_id: str) -> None:
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        existing = self._load_failed_ids()
        if internal_id.upper() not in existing:
            with self.failed_list_path.open("a", encoding="utf-8") as f:
                f.write(f"{internal_id.upper()}\n")

    def _clear_failed_ids(self) -> None:
        if self.failed_list_path.is_file():
            self.failed_list_path.unlink()

    def _save_state(self, state: dict[str, Any]) -> None:
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def _load_last_run_id(self) -> Optional[uuid.UUID]:
        if not self.state_path.is_file():
            return None
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            rid = state.get("run_id")
            return uuid.UUID(str(rid)) if rid else None
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    def build_mappings(self, db):
        from crop_monitoring.satellite_pipeline.harvest_field_mapping import (
            build_harvest_mappings,
            resolve_grower_ids_for_mappings,
        )

        report = build_harvest_mappings(
            self.kml_dir,
            data_root=self.mapping_dir,
            season_id="RABI_25_26",
            db=db,
        )
        return resolve_grower_ids_for_mappings(db, report)

    def run(self, config: RunConfig, *, resume_failed_only: bool = False) -> int:
        from crop_monitoring.satellite_pipeline.harvest_validation import (
            run_full_validation,
            write_validation_reports,
        )

        self.paths.ensure_runtime_dirs()
        explicit_run_id = config.run_id
        run_id = explicit_run_id
        if config.continue_last_run and run_id is None:
            run_id = self._load_last_run_id()
            if run_id:
                logger.info("Continuing previous run_id=%s", run_id)
        if run_id is None:
            run_id = uuid.uuid4()

        from core.db import SessionLocal

        db = SessionLocal()
        try:
            report = self.build_mappings(db)
            db.commit()
        except Exception:
            db.rollback()
            db.close()
            raise

        if not config.dry_run and (
            config.validate_only or not (config.limit or config.only_internal_ids)
        ):
            report = run_full_validation(report, self.kml_dir)
            write_validation_reports(report, self.report_dir)

        if config.validate_only:
            from crop_monitoring.satellite_pipeline.grower_validation import (
                write_grower_validation_reports,
            )

            db2 = SessionLocal()
            try:
                write_grower_validation_reports(db2, report, self.report_dir / "grower")
                db2.commit()
            finally:
                db2.close()
            db.close()
            return 0 if not report.errors else 1

        if config.dry_run:
            for iid in sorted(report.mappings.keys())[:20]:
                m = report.mappings[iid]
                logger.info("  %s → %s grower=%s", m.internal_id, m.file_name, m.grower_name)
            db.close()
            return 0

        items = sorted(report.mappings.values(), key=lambda m: m.internal_id)
        if config.only_internal_ids:
            allow = {x.strip().upper() for x in config.only_internal_ids}
            items = [m for m in items if m.internal_id in allow]
        if resume_failed_only:
            failed = self._load_failed_ids()
            if failed:
                items = [m for m in items if m.internal_id in failed]
                logger.info("Resume-failed mode: %d fields", len(items))
        elif config.resume and (config.continue_last_run or explicit_run_id is not None):
            from crop_monitoring.satellite_pipeline.checkpoint import list_complete_file_names

            done_names = list_complete_file_names(db, run_id)
            if done_names:
                before = len(items)
                items = [m for m in items if m.file_name not in done_names]
                logger.info(
                    "Skipping %d complete fields; %d remaining for run_id=%s",
                    before - len(items),
                    len(items),
                    run_id,
                )
        if config.limit:
            items = items[: config.limit]

        health = HealthSnapshot(total=len(items))
        health.write(self.health_path)

        from crop_monitoring.kml_parser import parse_kml
        from crop_monitoring.satellite_pipeline.field_context import FieldContext
        from crop_monitoring.satellite_pipeline.harvest_registry import persist_mapping_report
        from crop_monitoring.satellite_pipeline.orchestrator import SatelliteIngestionOrchestrator
        from crop_monitoring.database.sentinel_repositories import (
            create_ingestion_run,
            finish_ingestion_run,
        )
        from crop_monitoring.satellite_pipeline.cloud_config import S2CloudSettings
        from crop_monitoring.satellite_pipeline.grower_validation import (
            export_new_grower_ids,
            write_grower_validation_reports,
        )
        from shapely.geometry import shape

        persist_mapping_report(db, report)
        db.commit()

        create_ingestion_run(
            db,
            run_id=str(run_id),
            mode=f"harvest_{config.mode}",
            season_id=config.season_id,
            time_start=config.start,
            time_end=config.end,
        )
        db.commit()

        cloud = S2CloudSettings.from_env(
            {"max_scene_cloud_pct": config.max_cloud_cover}
            if config.max_cloud_cover is not None
            else None
        )
        orch = SatelliteIngestionOrchestrator(
            db,
            season_id=config.season_id,
            start_date=config.start,
            end_date=config.end,
            cloud_settings=cloud,
            run_id=run_id,
            batch_strategy=config.batch_strategy,  # type: ignore[arg-type]
            resume=config.resume,
            use_stac=config.use_stac,
            s1_s3_every_calendar_day=not config.s1_s3_on_s2_days_only,
        )

        ok = fail = 0
        self._save_state(
            {
                "run_id": str(run_id),
                "mode": config.mode,
                "start": config.start.isoformat(),
                "end": config.end.isoformat(),
                "total": len(items),
                "status": "running",
            }
        )

        for i, m in enumerate(items, 1):
            if m.validation_status == "error":
                fail_logger.warning("Skip invalid geometry %s", m.internal_id)
                health.failed += 1
                self._append_failed_id(m.internal_id)
                fail += 1
                continue

            if m.kml_path:
                kml_path = Path(m.kml_path)
            else:
                disk = m.legacy_file_name or f"{m.internal_id}.kml"
                kml_path = self.kml_dir / disk
            health.in_progress = m.internal_id
            health.write(self.health_path)

            t0 = time.perf_counter()
            success = False
            last_err: Optional[Exception] = None

            for attempt in range(1, config.max_retries_per_field + 2):
                if attempt > 1:
                    retry_logger.warning(
                        "Retry %s/%s for %s after %s",
                        attempt - 1,
                        config.max_retries_per_field,
                        m.internal_id,
                        last_err,
                    )
                    time.sleep(config.retry_backoff_seconds * (attempt - 1))

                try:
                    logger.info("[%s/%s] %s (%s)", i, len(items), m.file_name, m.internal_id)
                    api_logger.info("start field=%s file=%s", m.internal_id, m.file_name)
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
                    shape(geojson)
                    counts = orch.process_kml_file(
                        geojson=geojson,
                        file_name=m.file_name,
                        location_id=m.location_id,
                        field=ctx,
                        reprocess_only=(config.mode == "reprocess_raw"),
                    )
                    db.commit()
                    elapsed = time.perf_counter() - t0
                    perf_logger.info(
                        "field=%s elapsed_sec=%.1f counts=%s",
                        m.internal_id,
                        elapsed,
                        counts,
                    )
                    api_logger.info("done field=%s counts=%s", m.internal_id, counts)
                    logger.info("%s → %s", m.file_name, counts)
                    if counts.get("errors", 0) > 0:
                        fail += 1
                        health.failed += 1
                        self._append_failed_id(m.internal_id)
                        fail_logger.warning("%s errors in counts: %s", m.internal_id, counts)
                    else:
                        ok += 1
                        health.processed += 1
                    success = True
                    break
                except Exception as e:
                    last_err = e
                    db.rollback()
                    fail_logger.exception("%s attempt %s failed: %s", m.file_name, attempt, e)

            if not success:
                fail += 1
                health.failed += 1
                self._append_failed_id(m.internal_id)
                if not config.skip_errors:
                    break

            health.field_durations_sec.append(time.perf_counter() - t0)
            health.in_progress = None
            health.write(self.health_path)

            if config.api_delay_seconds > 0 and i < len(items):
                time.sleep(config.api_delay_seconds)

        finish_ingestion_run(
            db,
            str(run_id),
            status="completed" if fail == 0 else "partial",
            files_processed=ok,
            files_failed=fail,
        )
        db.commit()

        write_grower_validation_reports(db, report, self.report_dir / "grower")
        if report.created_grower_ids:
            export_new_grower_ids(
                db,
                report.created_grower_ids,
                self.report_dir / "grower" / "grower_newly_created_grower_ids.csv",
            )

        self._save_state(
            {
                "run_id": str(run_id),
                "status": "completed" if fail == 0 else "partial",
                "ok": ok,
                "fail": fail,
                "health": health.to_dict(),
            }
        )
        db.close()

        logger.info(
            "run_id=%s ok=%d fail=%d remaining=%d eta_sec=%s",
            run_id,
            ok,
            fail,
            health.remaining,
            health.eta_seconds,
        )
        return 0 if fail == 0 else 1
