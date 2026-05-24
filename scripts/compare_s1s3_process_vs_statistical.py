#!/usr/bin/env python3
"""
Compare S1/S3 daily values: legacy Process API (per-day) vs bulk Statistical API (P1D).

Use on lab with working SH credentials. Does NOT write to sentinel*_indices unless --write-test.

Examples:
  # Live API compare on 5 sample days (saves ~206 Process calls vs full 108)
  python scripts/compare_s1s3_process_vs_statistical.py \\
    --internal-id IND-KA-600048 --sample 5

  # Compare bulk Statistical vs rows already in DB (from prior Process run)
  python scripts/compare_s1s3_process_vs_statistical.py \\
    --internal-id IND-KA-600048 --db-only

  # Full calendar Process vs Statistical (216 Process calls — high PU)
  python scripts/compare_s1s3_process_vs_statistical.py \\
    --internal-id IND-KA-600048 --full
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:
    pass

import numpy as np
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

TOL_REL = 0.15  # 15% relative tolerance (Process lookback vs Statistical P1D mosaic differ)
TOL_ABS_SAR = 0.02
TOL_ABS_LST = 2.0
TOL_ABS_BT = 5.0


def _resolve_field(db, internal_id: str, kml_dir: Path) -> dict[str, Any]:
    from crop_monitoring.satellite_pipeline.harvest_field_mapping import (
        build_harvest_mappings,
    )

    report = build_harvest_mappings(kml_dir, data_root=kml_dir, season_id="RABI_25_26", db=db)
    m = report.mappings.get(internal_id.upper())
    if not m:
        raise SystemExit(f"Unknown internal_id: {internal_id}")
    kml_path = Path(m.kml_path) if m.kml_path else kml_dir / f"{m.internal_id}.kml"
    from crop_monitoring.kml_parser import parse_kml

    geojson, _ = parse_kml(kml_path)
    return {
        "internal_id": m.internal_id,
        "location_id": m.location_id,
        "file_name": m.file_name,
        "geojson": geojson,
    }


def _load_db_s1s3(db, location_id: str, file_name: str, season_id: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for sat, table, cols in (
        ("s1", "sentinel1_indices", "vv, vh, vv_db, vh_db"),
        ("s3", "sentinel3_indices", "s7, s8, s9, lst_celsius"),
    ):
        q = text(f"""
            SELECT acquisition_date::text, {cols}
            FROM operations.{table}
            WHERE location_id = :loc AND file_name = :fn
              AND season_id IS NOT DISTINCT FROM :sid
            ORDER BY acquisition_date
        """)
        for r in db.execute(q, {"loc": location_id, "fn": file_name, "sid": season_id}).fetchall():
            ad = str(r[0])[:10]
            rows.setdefault(ad, {"source": "db_process_era"})
            if sat == "s1":
                rows[ad].update({"vv": r[1], "vh": r[2], "vv_db": r[3], "vh_db": r[4]})
            else:
                rows[ad].update({"s7": r[1], "s8": r[2], "s9": r[3], "lst_c": r[4]})
    return rows


def _tuple_to_dict(ad: str, t: tuple) -> dict[str, Any]:
    s7, s8, s9, lst_c, vv, vh = (t + (None,) * 6)[:6]
    return {
        "acquisition_date": ad,
        "s7": s7,
        "s8": s8,
        "s9": s9,
        "lst_c": lst_c,
        "vv": vv,
        "vh": vh,
    }


def _fetch_process(geojson: dict, dates: list[str]) -> tuple[dict[str, dict], int]:
    from crop_monitoring.satellite_pipeline.orchestrator import _polygon_mean_s1_s3

    out: dict[str, dict] = {}
    for i, ad in enumerate(dates, 1):
        logger.info("Process API [%s/%s] %s", i, len(dates), ad)
        out[ad] = _tuple_to_dict(ad, _polygon_mean_s1_s3(geojson, ad))
    return out, len(dates) * 2


def _fetch_statistical(geojson: dict, start: str, end: str, calendar: list[str]) -> tuple[dict[str, dict], dict]:
    from crop_monitoring.satellite_pipeline.fetch_s1_s3_statistical import fetch_s1s3_bulk_calendar

    by_date, _s1_raw, _s3_raw, stats = fetch_s1s3_bulk_calendar(
        geojson, start, end, calendar, batch_strategy="quarterly"
    )
    out = {ad: _tuple_to_dict(ad, t) for ad, t in by_date.items()}
    return out, stats


def _close(a: Optional[float], b: Optional[float], *, abs_tol: float, rel_tol: float = TOL_REL) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if not (np.isfinite(a) and np.isfinite(b)):
        return False
    diff = abs(float(a) - float(b))
    scale = max(abs(float(a)), abs(float(b)), 1e-9)
    return diff <= abs_tol or diff / scale <= rel_tol


def _compare_day(proc: dict, stat: dict) -> dict[str, Any]:
    fields = [
        ("vv", TOL_ABS_SAR),
        ("vh", TOL_ABS_SAR),
        ("s7", TOL_ABS_BT),
        ("s8", TOL_ABS_BT),
        ("s9", TOL_ABS_BT),
        ("lst_c", TOL_ABS_LST),
    ]
    mismatches = []
    for key, atol in fields:
        p = proc.get(key)
        s = stat.get(key)
        ok = _close(p, s, abs_tol=atol)
        if not ok:
            mismatches.append(
                {
                    "field": key,
                    "process": p,
                    "statistical": s,
                    "delta": None if p is None or s is None else float(s) - float(p),
                }
            )
    proc_has = any(proc.get(k) is not None for k in ("vv", "vh", "s8", "s9"))
    stat_has = any(stat.get(k) is not None for k in ("vv", "vh", "s8", "s9"))
    return {
        "null_parity": proc_has == stat_has,
        "value_match": len(mismatches) == 0,
        "mismatches": mismatches,
    }


def _pick_sample_dates(calendar: list[str], n: int) -> list[str]:
    if n >= len(calendar):
        return calendar
    idx = np.linspace(0, len(calendar) - 1, n, dtype=int)
    return [calendar[i] for i in idx]


def main() -> int:
    p = argparse.ArgumentParser(description="Compare S1/S3 Process vs Statistical retrieval")
    p.add_argument("--internal-id", required=True)
    p.add_argument("--season-id", default="RABI_25_26")
    p.add_argument("--start", default="2025-12-01")
    p.add_argument("--end", default="2026-03-18")
    p.add_argument("--kml-dir", default=None)
    p.add_argument("--sample", type=int, default=5, help="Process API sample days (default 5)")
    p.add_argument("--full", action="store_true", help="Process API for all calendar days (216 calls)")
    p.add_argument("--db-only", action="store_true", help="Compare DB rows vs Statistical only (no Process API)")
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    kml_dir = Path(args.kml_dir or __import__("os").environ.get(
        "SATELLITE_KML_DIR", "/home/madanm/kml_files/harvest_all_fields"
    ))

    from core.db import SessionLocal

    db = SessionLocal()
    try:
        field = _resolve_field(db, args.internal_id, kml_dir)
    finally:
        db.close()

    from crop_monitoring.satellite_pipeline.temporal_batch import calendar_dates_inclusive

    calendar = calendar_dates_inclusive(date.fromisoformat(args.start), date.fromisoformat(args.end))

    logger.info("Field %s → %s", field["internal_id"], field["file_name"])
    logger.info("Calendar days: %d", len(calendar))

    # --- Statistical (new) ---
    try:
        stat_by_date, api_stats = _fetch_statistical(
            field["geojson"], args.start, args.end, calendar
        )
    except Exception as e:
        logger.error("Statistical bulk fetch failed: %s", e)
        return 2

    stat_calls = api_stats.get("s1_requests", 0) + api_stats.get("s3_requests", 0)
    logger.info(
        "Statistical API: %s requests, %d calendar days expanded",
        stat_calls,
        len(stat_by_date),
    )

    proc_by_date: dict[str, dict] = {}
    process_calls = 0
    compare_label = "process_vs_statistical"

    if args.db_only:
        compare_label = "db_vs_statistical"
        db = SessionLocal()
        try:
            proc_by_date = _load_db_s1s3(
                db, field["location_id"], field["file_name"], args.season_id
            )
        finally:
            db.close()
        process_calls = len(calendar) * 2  # equivalent if full process run
        logger.info("Loaded %d days from DB (prior ingestion)", len(proc_by_date))
    else:
        dates = calendar if args.full else _pick_sample_dates(calendar, args.sample)
        logger.info(
            "Process API: fetching %d days (%s)",
            len(dates),
            "full calendar" if args.full else f"sample of {args.sample}",
        )
        try:
            proc_by_date, process_calls = _fetch_process(field["geojson"], dates)
        except Exception as e:
            logger.error("Process API fetch failed: %s", e)
            return 2

    # Compare on intersection of dates
    compare_dates = sorted(set(proc_by_date) & set(stat_by_date))
    if not args.db_only and not args.full:
        compare_dates = sorted(proc_by_date.keys())

    day_reports = []
    null_parity_ok = value_match_ok = 0
    for ad in compare_dates:
        cmp = _compare_day(proc_by_date.get(ad, {}), stat_by_date.get(ad, {}))
        day_reports.append({"date": ad, **cmp})
        if cmp["null_parity"]:
            null_parity_ok += 1
        if cmp["value_match"]:
            value_match_ok += 1

    stat_dates_with_data = sum(
        1
        for d in calendar
        if any(stat_by_date.get(d, {}).get(k) is not None for k in ("vv", "vh", "s8", "s9"))
    )
    proc_dates_with_data = sum(
        1
        for d in compare_dates
        if any(proc_by_date.get(d, {}).get(k) is not None for k in ("vv", "vh", "s8", "s9"))
    )

    report = {
        "compare_mode": compare_label,
        "field": {
            "internal_id": field["internal_id"],
            "location_id": field["location_id"],
            "file_name": field["file_name"],
        },
        "season_id": args.season_id,
        "calendar_days": len(calendar),
        "api_calls": {
            "process_equivalent_full_season": len(calendar) * 2,
            "process_used_this_run": process_calls,
            "statistical_s1_s3_requests": stat_calls,
            "pu_reduction_pct": round(
                100.0 * (1.0 - stat_calls / max(len(calendar) * 2, 1)), 1
            ),
        },
        "coverage": {
            "statistical_days_with_any_data": stat_dates_with_data,
            "process_days_with_any_data": proc_dates_with_data,
            "days_compared": len(compare_dates),
        },
        "parity": {
            "null_presence_match_days": null_parity_ok,
            "value_match_days": value_match_ok,
            "null_parity_pct": round(100.0 * null_parity_ok / max(len(compare_dates), 1), 1),
            "value_match_pct": round(100.0 * value_match_ok / max(len(compare_dates), 1), 1),
        },
        "day_details": [d for d in day_reports if not d["value_match"]][:20],
        "note": (
            "Process API uses 12-day S1 lookback + padded S3 window per day; "
            "Statistical API uses P1D mosaic means — exact pixel parity is not expected, "
            "but null/data presence and approximate magnitudes should align."
        ),
    }

    text_out = json.dumps(report, indent=2, default=str)
    print(text_out)
    out_path = args.out or _root / "reports" / "harvest" / f"s1s3_compare_{field['internal_id']}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text_out + "\n", encoding="utf-8")
    print(f"\nWrote {out_path}")

    ok = report["parity"]["null_parity_pct"] >= 80.0
    if args.db_only:
        ok = ok and len(proc_by_date) >= len(calendar) * 0.9
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
