#!/usr/bin/env python3
"""
Parallel validation: compare operations.sentinel2_indices vs crop_indices for same keys.

Usage:
  python scripts/compare_v2_legacy_indices.py --season-id RABI_25_26 --limit 500
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season-id", required=True)
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--ndvi-tol", type=float, default=0.05)
    args = ap.parse_args()

    from sqlalchemy import text
    from core.db import SessionLocal

    db = SessionLocal()
    rows = db.execute(
        text("""
            SELECT
                s.location_id,
                s.file_name,
                s.acquisition_date::text AS ad,
                s.ndvi AS s2_ndvi,
                c.ndvi AS leg_ndvi,
                s.ndmi AS s2_ndmi,
                c.ndmi AS leg_ndmi,
                s3.lst_celsius AS s3_lst,
                COALESCE(c.lst_celsius, c.lst_c) AS leg_lst,
                s1.vv_db AS s1_vv,
                COALESCE(c.vv_db, c.vv) AS leg_vv
            FROM operations.sentinel2_indices s
            JOIN operations.v_crop_indices_observation c
              ON c.location_id = s.location_id
             AND c.file_name = s.file_name
             AND c.season_id IS NOT DISTINCT FROM s.season_id
             AND c.observation_date = s.acquisition_date
            LEFT JOIN operations.sentinel1_indices s1
              ON s1.location_id = s.location_id
             AND s1.file_name = s.file_name
             AND s1.season_id IS NOT DISTINCT FROM s.season_id
             AND s1.acquisition_date = s.acquisition_date
            LEFT JOIN operations.sentinel3_indices s3
              ON s3.location_id = s.location_id
             AND s3.file_name = s.file_name
             AND s3.season_id IS NOT DISTINCT FROM s.season_id
             AND s3.acquisition_date = s.acquisition_date
            WHERE s.season_id IS NOT DISTINCT FROM :sid
            LIMIT :lim
        """),
        {"sid": args.season_id, "lim": args.limit},
    ).fetchall()

    mism_ndvi = mism_ndmi = mism_lst = mism_vv = 0
    compared = 0
    for r in rows:
        compared += 1
        s2n, ln = r[3], r[4]
        if s2n is not None and ln is not None and abs(float(s2n) - float(ln)) > args.ndvi_tol:
            mism_ndvi += 1
        if r[5] is not None and r[6] is not None and abs(float(r[5]) - float(r[6])) > args.ndvi_tol:
            mism_ndmi += 1
        if r[7] is not None and r[8] is not None and abs(float(r[7]) - float(r[8])) > 1.0:
            mism_lst += 1
        if r[9] is not None and r[10] is not None and abs(float(r[9]) - float(r[10])) > 0.5:
            mism_vv += 1

    print(f"Compared rows: {compared}")
    print(f"NDVI mismatches (>{args.ndvi_tol}): {mism_ndvi}")
    print(f"NDMI mismatches: {mism_ndmi}")
    print(f"LST mismatches (>1C): {mism_lst}")
    print(f"VV_dB mismatches (>0.5dB): {mism_vv}")
    db.close()
    sys.exit(0 if mism_ndvi == 0 else 1)


if __name__ == "__main__":
    main()
