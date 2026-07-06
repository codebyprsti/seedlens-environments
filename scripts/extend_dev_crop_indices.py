#!/usr/bin/env python3
"""
Extend dev_crop_intel.crop_indices through harvest (Mar 18 -> May 31).

Steps:
  1. Upsert operations.crop_indices rows in the date window into dev_crop_intel.
  2. Copernicus backfill (operations.crop_indices) for fields still short of the window.
  3. Re-sync the date window into dev_crop_intel.

Example:
  python scripts/extend_dev_crop_indices.py
  python scripts/extend_dev_crop_indices.py --sync-only
  python scripts/extend_dev_crop_indices.py --fetch-only --limit 10
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_SEASON = "RABI_25_26"
DEFAULT_START = "2026-03-18"
DEFAULT_END = "2026-05-31"
S3_PREFIX = "seedworks/kml_files/"


def _files_needing_fetch(db, *, season_id: str, start: str, end: str) -> list[str]:
    from sqlalchemy import text

    rows = db.execute(
        text(
            """
            SELECT d.file_name
            FROM dev_crop_intel.crop_indices d
            WHERE d.season_id = :sid
            GROUP BY d.file_name
            HAVING COALESCE(
                     (SELECT MAX(o.index_date) FROM operations.crop_indices o
                      WHERE o.file_name = d.file_name AND o.season_id = :sid),
                     MAX(d.observation_date)
                   ) < CAST(:end AS date)
            ORDER BY d.file_name
            """
        ),
        {"sid": season_id, "end": end},
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def _run_sync(*, season_id: str, date_from: str, date_to: str) -> int:
    cmd = [
        sys.executable,
        str(_ROOT / "scripts" / "sync_crop_indices_to_dev_crop_intel.py"),
        "--all-season",
        "--season-id",
        season_id,
        "--date-from",
        date_from,
        "--date-to",
        date_to,
    ]
    logger.info("Running sync: %s", " ".join(cmd))
    return subprocess.call(cmd)


def _coverage_summary(db, *, season_id: str, date_from: str, date_to: str) -> None:
    from sqlalchemy import text

    r = db.execute(
        text(
            """
            SELECT COUNT(DISTINCT file_name) AS fields,
                   MIN(observation_date) AS min_d,
                   MAX(observation_date) AS max_d,
                   COUNT(*) AS n
            FROM dev_crop_intel.crop_indices
            WHERE season_id = :sid
              AND observation_date >= CAST(:df AS date)
              AND observation_date <= CAST(:de AS date)
            """
        ),
        {"sid": season_id, "df": date_from, "de": date_to},
    ).mappings().first()
    print(f"dev_crop_intel [{date_from}..{date_to}]: {dict(r)}")

    short = db.execute(
        text(
            """
            SELECT COUNT(*) FROM (
              SELECT file_name FROM dev_crop_intel.crop_indices
              WHERE season_id = :sid
              GROUP BY file_name
              HAVING MAX(observation_date) < CAST(:de AS date)
            ) t
            """
        ),
        {"sid": season_id, "de": date_to},
    ).scalar()
    print(f"dev fields with max < {date_to}: {short}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extend dev_crop_intel.crop_indices to May end.")
    parser.add_argument("--season-id", default=DEFAULT_SEASON)
    parser.add_argument("--start", default=DEFAULT_START, help="Copernicus window start")
    parser.add_argument("--end", default=DEFAULT_END, help="Copernicus window end")
    parser.add_argument("--s3-prefix", default=S3_PREFIX)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sync-only", action="store_true")
    parser.add_argument("--fetch-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--log-file",
        default=str(_ROOT / "logs" / "extend_dev_crop_indices.log"),
    )
    args = parser.parse_args()

    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    from core.db import SessionLocal

    if not args.fetch_only:
        print("=== Phase 1: sync operations -> dev_crop_intel ===")
        rc = _run_sync(season_id=args.season_id, date_from=args.start, date_to=args.end)
        if rc != 0:
            return rc

    if args.sync_only:
        db = SessionLocal()
        try:
            _coverage_summary(db, season_id=args.season_id, date_from=args.start, date_to=args.end)
        finally:
            db.close()
        return 0

    db = SessionLocal()
    try:
        need = _files_needing_fetch(db, season_id=args.season_id, start=args.start, end=args.end)
    finally:
        db.close()

    if args.limit:
        need = need[: args.limit]
    print(f"\n=== Phase 2: Copernicus extend for {len(need)} file(s) [{args.start}..{args.end}] ===")
    if not need:
        print("No files need Copernicus fetch.")
        if not args.fetch_only:
            return 0
        db = SessionLocal()
        try:
            _coverage_summary(db, season_id=args.season_id, date_from=args.start, date_to=args.end)
        finally:
            db.close()
        return 0

    if args.dry_run:
        for fn in need[:20]:
            print(f"  {fn}")
        if len(need) > 20:
            print(f"  ... and {len(need) - 20} more")
        return 0

    # Write file list for batch --only-file (avoid huge argv)
    list_path = _ROOT / "tmp" / "extend_dev_crop_files.txt"
    list_path.parent.mkdir(parents=True, exist_ok=True)
    list_path.write_text("\n".join(need) + "\n", encoding="utf-8")

    # Process in chunks via repeated --only-file batches
    chunk_size = 100
    total_rc = 0
    for i in range(0, len(need), chunk_size):
        chunk = need[i : i + chunk_size]
        cmd = [
            sys.executable,
            str(_ROOT / "scripts" / "run_crop_analysis_s3_batch.py"),
            "--prefix",
            args.s3_prefix,
            "--start",
            args.start,
            "--end",
            args.end,
            "--season-id",
            args.season_id,
            "--extend-dates",
            "--log-file",
            str(log_path),
        ]
        for fn in chunk:
            cmd.extend(["--only-file", fn])
        logger.info("Batch chunk %d-%d of %d", i + 1, i + len(chunk), len(need))
        rc = subprocess.call(cmd)
        if rc != 0:
            total_rc = rc
            logger.warning("Chunk returned exit code %s (continuing)", rc)
        # Incremental sync so dev_crop_intel stays current during long runs
        _run_sync(season_id=args.season_id, date_from=args.start, date_to=args.end)

    print("\n=== Phase 3: re-sync operations -> dev_crop_intel ===")
    rc = _run_sync(season_id=args.season_id, date_from=args.start, date_to=args.end)
    if rc != 0 and total_rc == 0:
        total_rc = rc

    db = SessionLocal()
    try:
        _coverage_summary(db, season_id=args.season_id, date_from=args.start, date_to=args.end)
    finally:
        db.close()

    return total_rc


if __name__ == "__main__":
    raise SystemExit(main())
