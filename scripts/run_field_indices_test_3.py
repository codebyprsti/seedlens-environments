#!/usr/bin/env python3
"""
Smoke-test: process 3 local KMLs and write to operations.crop_indices + operations.field_indices.

Uses a dedicated season_id (default TEST_FIELD_3KML) so the batch does not skip files that
were already ingested under RABI_25_26.

If crop_indices insert fails with UniqueViolation (same location/date/variety/file as an
existing row — your DB may not include season_id in that unique key), load field_indices
from data already in crop_indices instead:

  python scripts/backfill_field_indices_for_files.py --from-demo-kml 3

Prerequisites:
  - sql/create_field_indices_table.sql applied (operations.field_indices exists)
  - DB + Sentinel Hub credentials as for run_crop_analysis_s3_batch.py
  - FIELD_INDICES_MIRROR=1 (default)

Example:
  python scripts/run_field_indices_test_3.py
  python scripts/run_field_indices_test_3.py --local-dir D:/kmls --season-id MY_TEST_01
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    p = argparse.ArgumentParser(description="Test load 3 KMLs into crop_indices + field_indices.")
    p.add_argument(
        "--local-dir",
        type=str,
        default=str(_ROOT / "demo_raw_test_kml"),
        help="Directory containing .kml files (default: repo demo_raw_test_kml)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Number of KML files to process (default: 3)",
    )
    p.add_argument(
        "--season-id",
        type=str,
        default="TEST_FIELD_3KML",
        help="Season id for new rows (default: TEST_FIELD_3KML)",
    )
    args = p.parse_args()

    batch = _ROOT / "scripts" / "run_crop_analysis_s3_batch.py"
    cmd = [
        sys.executable,
        str(batch),
        "--local-dir",
        str(Path(args.local_dir).resolve()),
        "--limit",
        str(args.limit),
        "--season-id",
        args.season_id.strip(),
        "--mode",
        "crop_indices",
    ]
    print("Running:", " ".join(cmd))
    raise SystemExit(subprocess.call(cmd, cwd=str(_ROOT)))


if __name__ == "__main__":
    main()
