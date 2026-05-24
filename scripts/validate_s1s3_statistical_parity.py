#!/usr/bin/env python3
"""
Compare S1/S3 Statistical bulk vs legacy Process-API expectations for one field.

Reports API call counts, DB row counts, and date coverage (no schema changes).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:
    pass

from sqlalchemy import text

from core.db import SessionLocal
from crop_monitoring.satellite_pipeline.reprocess import load_raw_rows
from crop_monitoring.satellite_pipeline.temporal_batch import calendar_dates_inclusive


def main() -> int:
    p = argparse.ArgumentParser(description="S1/S3 Statistical vs Process parity report")
    p.add_argument("--location-id", required=True)
    p.add_argument("--file-name", required=True)
    p.add_argument("--season-id", default="RABI_25_26")
    p.add_argument("--start", default="2025-12-01")
    p.add_argument("--end", default="2026-03-18")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    cal = calendar_dates_inclusive(date.fromisoformat(args.start), date.fromisoformat(args.end))
    process_calls_equiv = len(cal) * 2

    db = SessionLocal()
    try:
        s1_rows = db.execute(
            text("""
                SELECT acquisition_date::text, vv, vh FROM operations.sentinel1_indices
                WHERE location_id = :loc AND file_name = :fn
                  AND season_id IS NOT DISTINCT FROM :sid
                ORDER BY acquisition_date
            """),
            {"loc": args.location_id, "fn": args.file_name, "sid": args.season_id},
        ).fetchall()
        s3_rows = db.execute(
            text("""
                SELECT acquisition_date::text, s8, s9, lst_celsius FROM operations.sentinel3_indices
                WHERE location_id = :loc AND file_name = :fn
                  AND season_id IS NOT DISTINCT FROM :sid
                ORDER BY acquisition_date
            """),
            {"loc": args.location_id, "fn": args.file_name, "sid": args.season_id},
        ).fetchall()

        api_stats = None
        retrieval = "unknown"
        for row in load_raw_rows(db, location_id=args.location_id, file_name=args.file_name):
            src = row.get("source") or ""
            raw = row.get("raw_response") or {}
            if isinstance(raw, dict) and raw.get("api_stats"):
                api_stats = raw["api_stats"]
            if "statistical" in src:
                retrieval = "statistical_bulk"
            elif "copernicus_s1" in src or "copernicus_s3" in src:
                retrieval = "process_per_day"

        s1_dates = {r[0][:10] for r in s1_rows}
        s3_dates = {r[0][:10] for r in s3_rows}
        cal_set = set(cal)

        report = {
            "location_id": args.location_id,
            "file_name": args.file_name,
            "season_id": args.season_id,
            "calendar_days": len(cal),
            "retrieval_mode": retrieval,
            "process_api_calls_equivalent": process_calls_equiv,
            "statistical_api_stats": api_stats,
            "db_s1_rows": len(s1_rows),
            "db_s3_rows": len(s3_rows),
            "s1_dates_in_calendar": len(s1_dates & cal_set),
            "s3_dates_in_calendar": len(s3_dates & cal_set),
            "s1_missing_calendar_dates": sorted(cal_set - s1_dates),
            "s3_missing_calendar_dates": sorted(cal_set - s3_dates),
            "s1_null_vv_days": sum(1 for r in s1_rows if r[1] is None),
            "s3_null_lst_days": sum(1 for r in s3_rows if r[3] is None),
            "parity_ok": (
                len(s1_dates & cal_set) == len(cal)
                and len(s3_dates & cal_set) == len(cal)
            ),
        }
        if api_stats:
            report["pu_reduction_pct"] = round(
                100.0
                * (1.0 - (api_stats.get("s1_requests", 0) + api_stats.get("s3_requests", 0)) / max(process_calls_equiv, 1)),
                1,
            )

        text_out = json.dumps(report, indent=2)
        print(text_out)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text_out + "\n", encoding="utf-8")
            print(f"Wrote {args.out}")
        return 0 if report["parity_ok"] else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
