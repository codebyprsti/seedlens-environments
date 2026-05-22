"""KML listing, subprocess invocation, optional parallelism (batch logic unchanged)."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from copernicus_pipeline.config import PipelinePaths, repo_root_from_package
from copernicus_pipeline.errors import ValidationError

logger = logging.getLogger(__name__)


def list_kml_files(local_dir: Path) -> list[Path]:
    """Same rules as ``list_kml_from_local`` in the batch script (non-empty files, recursive)."""
    root = local_dir.resolve()
    if not root.is_dir():
        return []
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
    return out


def normalize_basename(name: str) -> str:
    """Match batch ``--only-file`` filter: collapse whitespace, strip."""
    s = name.replace("+", " ").strip()
    return re.sub(r"\s+", " ", s).lower()


def validate_iso_date(label: str, value: str) -> str:
    from datetime import date

    s = (value or "").strip()
    if len(s) != 10:
        raise ValidationError(f"{label} must be YYYY-MM-DD, got {value!r}")
    try:
        date.fromisoformat(s)
    except ValueError as e:
        raise ValidationError(f"{label} invalid date: {value!r}") from e
    return s


def map_mode(cli_mode: str) -> str:
    if cli_mode in ("default", "crop_indices"):
        return "crop_indices"
    if cli_mode == "locations_only":
        return "locations_only"
    if cli_mode in ("satellite_v2", "v2", "reprocess_raw"):
        return cli_mode if cli_mode == "reprocess_raw" else "satellite_v2"
    raise ValidationError(f"Unknown mode {cli_mode!r}")


@dataclass
class BatchArgs:
    kml_dir: Path
    start: str
    end: str
    mode: str = "crop_indices"
    season_id: Optional[str] = None
    limit: Optional[int] = None
    only_files: Optional[Sequence[str]] = None
    dry_run: bool = False
    log_file: Optional[Path] = None
    geocode_delay: Optional[float] = None
    skip_satellite_raw: bool = False


def build_argv(paths: PipelinePaths, args: BatchArgs, only_subset: Optional[Sequence[str]] = None) -> list[str]:
    """Build argv for ``python scripts/run_crop_analysis_s3_batch.py``."""
    batch = paths.batch_script
    if not batch.is_file():
        raise ValidationError(
            f"Batch script not found at {batch}. Install editable from the SeedIQ-Prod repository root."
        )
    argv: list[str] = [
        sys.executable,
        str(batch),
        "--local-dir",
        str(Path(args.kml_dir).resolve()),
        "--start",
        args.start,
        "--end",
        args.end,
        "--mode",
        args.mode,
    ]
    if args.season_id:
        argv.extend(["--season-id", args.season_id])
    if args.limit is not None:
        argv.extend(["--limit", str(args.limit)])
    if args.dry_run:
        argv.append("--dry-run")
    if args.log_file:
        argv.extend(["--log-file", str(Path(args.log_file).resolve())])
    if args.geocode_delay is not None:
        argv.extend(["--geocode-delay", str(args.geocode_delay)])
    files = list(only_subset) if only_subset is not None else (args.only_files or [])
    for name in files:
        n = str(name).strip()
        if n:
            argv.extend(["--only-file", n])
    return argv


def run_subprocess(paths: PipelinePaths, argv: list[str], extra_env: Optional[dict[str, str]] = None) -> int:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    if env.get("SKIP_SATELLITE_RAW_OBSERVATION", "").lower() in ("1", "true", "yes"):
        logger.info("SKIP_SATELLITE_RAW_OBSERVATION enabled for batch subprocess")
    logger.debug("cwd=%s cmd=%s", paths.repo_root, argv)
    proc = subprocess.run(argv, cwd=str(paths.repo_root), env=env)
    return int(proc.returncode)


def run_batch(args: BatchArgs, *, jobs: int = 1, progress: bool = False) -> int:
    """
    Run the batch once (jobs==1) or one subprocess per KML file in parallel (jobs>1).

    Parallel mode ignores ``args.limit`` for splitting; apply limit to the file list first.
    """
    paths = PipelinePaths(repo_root=repo_root_from_package())
    kml_dir = Path(args.kml_dir).resolve()
    if not kml_dir.is_dir():
        raise ValidationError(f"--kml-dir is not a directory: {kml_dir}")

    extra: dict[str, str] = {}
    if args.skip_satellite_raw:
        extra["SKIP_SATELLITE_RAW_OBSERVATION"] = "1"

    if jobs < 1:
        raise ValidationError("--jobs must be >= 1")
    if jobs == 1:
        return run_subprocess(paths, build_argv(paths, args), extra_env=extra)

    all_paths = list_kml_files(kml_dir)
    if args.only_files:
        allow = {normalize_basename(n) for n in args.only_files if n and str(n).strip()}
        all_paths = [p for p in all_paths if normalize_basename(p.name) in allow]
    if args.limit is not None:
        all_paths = all_paths[: args.limit]
    if not all_paths:
        logger.warning("No KML files to process under %s", kml_dir)
        return 0

    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = None  # type: ignore[misc, assignment]

    def _one(p: Path) -> tuple[str, int]:
        sub = BatchArgs(
            kml_dir=args.kml_dir,
            start=args.start,
            end=args.end,
            mode=args.mode,
            season_id=args.season_id,
            limit=None,
            only_files=[p.name],
            dry_run=args.dry_run,
            log_file=args.log_file,
            geocode_delay=args.geocode_delay,
            skip_satellite_raw=args.skip_satellite_raw,
        )
        code = run_subprocess(paths, build_argv(paths, sub, only_subset=[p.name]), extra_env=extra)
        return p.name, code

    max_workers = min(jobs, len(all_paths))
    results: list[tuple[str, int]] = []
    paths_list = list(all_paths)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_one, p) for p in paths_list]
        done_iter = as_completed(futures)
        if progress and tqdm is not None:
            done_iter = tqdm(done_iter, total=len(futures), desc="KML batches", unit="file")
        for fut in done_iter:
            name, code = fut.result()
            results.append((name, code))
            if code != 0:
                logger.error("Batch failed for %s (exit %s)", name, code)

    worst = max((c for _, c in results), default=0)
    return worst
