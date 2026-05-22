"""High-level entry: validate config and invoke the batch runner (satellite logic stays in ``scripts/``)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

from copernicus_pipeline.db import assert_database_configured, assert_sentinel_configured
from copernicus_pipeline.errors import ValidationError
from copernicus_pipeline.runner import BatchArgs, map_mode, run_batch, validate_iso_date

logger = logging.getLogger(__name__)


def run_pipeline(
    *,
    kml_dir: Path,
    start: str,
    end: str,
    mode: str = "default",
    season_id: Optional[str] = None,
    limit: Optional[int] = None,
    only_files: Optional[Sequence[str]] = None,
    dry_run: bool = False,
    log_file: Optional[Path] = None,
    geocode_delay: Optional[float] = None,
    skip_satellite_raw: bool = False,
    jobs: int = 1,
    progress: bool = False,
    skip_env_checks: bool = False,
    batch_strategy: str = "quarterly",
    maxcc: float = 60.0,
    use_stac: bool = True,
) -> int:
    """
    Run the Copernicus KML pipeline.

    Returns process exit code from the batch subprocess (0 = success).
    """
    start_s = validate_iso_date("--start", start)
    end_s = validate_iso_date("--end", end)
    if limit is not None and limit < 0:
        raise ValidationError("--limit must be non-negative")
    mapped = map_mode(mode)
    normalized_only = None
    if only_files:
        normalized_only = [str(x).replace("+", " ").strip() for x in only_files if str(x).strip()]
    sid = (season_id or "").strip() or None

    if mapped in ("satellite_v2", "reprocess_raw"):
        from copernicus_pipeline.pipeline_v2 import run_pipeline_v2

        return run_pipeline_v2(
            kml_dir=kml_dir,
            start=start_s,
            end=end_s,
            season_id=sid,
            limit=limit,
            only_files=normalized_only,
            dry_run=dry_run,
            mode="reprocess_raw" if mapped == "reprocess_raw" else "incremental",
            batch_strategy=batch_strategy,
            maxcc=maxcc,
            use_stac=use_stac,
            skip_env_checks=skip_env_checks,
        )

    if not skip_env_checks:
        if dry_run:
            logger.info("Dry run: skipping DB/Sentinel env checks")
        else:
            assert_database_configured()
            if mapped == "crop_indices":
                assert_sentinel_configured()

    batch = BatchArgs(
        kml_dir=Path(kml_dir),
        start=start_s,
        end=end_s,
        mode=mapped,
        season_id=sid,
        limit=limit,
        only_files=normalized_only,
        dry_run=dry_run,
        log_file=log_file,
        geocode_delay=geocode_delay,
        skip_satellite_raw=skip_satellite_raw,
    )
    return run_batch(batch, jobs=jobs, progress=progress)
