#!/usr/bin/env python3
"""
Read-only comparison: ``copernicus_test_output.csv`` (from ``test_copernicus_locations.py``)
vs ``operations.crop_indices`` for the same ``(location_id, observation_date)``.

When multiple KML rows exist per day, picks one deterministic row:
``DISTINCT ON (location_id, obs_day) ... ORDER BY file_name ASC``.

Does not modify satellite logic or database.

Usage:
  python scripts/compare_copernicus_test_to_db.py \\
    --test-csv copernicus_test_output.csv \\
    --output copernicus_comparison_output.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

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
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Numeric tolerances (test uses centroid buffer; DB uses KML polygon — expect divergence).
TOL_NDVI = 0.01
TOL_EVI = 0.015
TOL_LST = 0.5
TOL_SAR_DB = 0.15

OUT_FIELDNAMES = (
    "location_id",
    "observation_date",
    "ndvi_db",
    "ndvi_test",
    "ndvi_diff",
    "evi_db",
    "evi_test",
    "evi_diff",
    "lst_db",
    "lst_test",
    "lst_diff",
    "sar_db",
    "sar_test",
    "mismatch_flag",
)


def _parse_float(s: Any) -> Optional[float]:
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _format_sar(vv_db: Optional[float], vh_db: Optional[float]) -> str:
    if vv_db is None and vh_db is None:
        return ""
    parts = []
    if vv_db is not None:
        parts.append(f"VV_dB={vv_db:.4f}")
    if vh_db is not None:
        parts.append(f"VH_dB={vh_db:.4f}")
    return ";".join(parts)


_RE_VV = re.compile(r"VV_dB\s*=\s*([+-]?\d+(?:\.\d+)?)", re.I)
_RE_VH = re.compile(r"VH_dB\s*=\s*([+-]?\d+(?:\.\d+)?)", re.I)


def _parse_sar_string(s: str) -> tuple[Optional[float], Optional[float]]:
    if not s or not str(s).strip():
        return None, None
    t = str(s).strip()
    vv_m = _RE_VV.search(t)
    vh_m = _RE_VH.search(t)
    vv = float(vv_m.group(1)) if vv_m else None
    vh = float(vh_m.group(1)) if vh_m else None
    return vv, vh


def _sar_mismatch(vv_a: Optional[float], vh_a: Optional[float], vv_b: Optional[float], vh_b: Optional[float]) -> bool:
    def bad(x: Optional[float], y: Optional[float]) -> bool:
        if x is None and y is None:
            return False
        if x is None or y is None:
            return True
        return abs(float(x) - float(y)) > TOL_SAR_DB

    return bad(vv_a, vv_b) or bad(vh_a, vh_b)


def _crop_indices_sar_column_names(db) -> tuple[str, str]:
    from sqlalchemy import text

    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'operations'
              AND table_name = 'crop_indices'
              AND column_name IN ('vv_db', 'vh_db', 'vv', 'vh')
            """
        )
    ).fetchall()
    cols = {str(r[0]) for r in rows if r and r[0]}
    vv_c = "vv_db" if "vv_db" in cols else "vv"
    vh_c = "vh_db" if "vh_db" in cols else "vh"
    return vv_c, vh_c


def load_test_rows(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Key (location_id, YYYY-MM-DD) -> row dict from test CSV."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            lid = (row.get("location_id") or "").strip()
            od = (row.get("observation_date") or "").strip()[:10]
            if not lid or not od:
                continue
            out[(lid, od)] = row
    return out


def fetch_db_rows(
    db,
    location_ids: list[str],
    *,
    season_id: Optional[str] = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """
    One row per (location_id, observation_date) via DISTINCT ON.
    """
    from crop_monitoring.insert_validator import crop_indices_observation_date_column
    from sqlalchemy import bindparam, text

    dcol = crop_indices_observation_date_column(db)
    vv_c, vh_c = _crop_indices_sar_column_names(db)

    season_sql = ""
    params: dict[str, Any] = {"lids": location_ids}
    if season_id:
        season_sql = " AND season_id = :season_id "
        params["season_id"] = season_id

    q = (
        text(
            f"""
        SELECT DISTINCT ON (location_id, ({dcol}::date))
            location_id,
            ({dcol}::date)::text AS observation_date,
            file_name,
            ndvi,
            evi,
            lst_celsius,
            {vv_c} AS vv_db_val,
            {vh_c} AS vh_db_val
        FROM operations.crop_indices
        WHERE location_id IN :lids
        {season_sql}
        ORDER BY location_id, ({dcol}::date), file_name ASC
        """
        ).bindparams(bindparam("lids", expanding=True))
    )
    rows = db.execute(q, params).mappings().all()
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for m in rows:
        lid = str(m["location_id"]).strip()
        od = str(m["observation_date"]).strip()[:10]
        out[(lid, od)] = dict(m)
    return out


def _diff_str(a: Optional[float], b: Optional[float]) -> str:
    if a is None or b is None:
        return ""
    return f"{float(a) - float(b):.6f}".rstrip("0").rstrip(".")


def build_mismatch_flag(
    *,
    ndvi_db: Optional[float],
    ndvi_te: Optional[float],
    evi_db: Optional[float],
    evi_te: Optional[float],
    lst_db: Optional[float],
    lst_te: Optional[float],
    sar_db_vv: Optional[float],
    sar_db_vh: Optional[float],
    sar_te_vv: Optional[float],
    sar_te_vh: Optional[float],
    missing: set[str],
) -> str:
    flags: list[str] = sorted(missing)
    if ndvi_db is not None and ndvi_te is not None and abs(ndvi_db - ndvi_te) > TOL_NDVI:
        flags.append("NDVI_OUT_OF_TOL")
    if evi_db is not None and evi_te is not None and abs(evi_db - evi_te) > TOL_EVI:
        flags.append("EVI_OUT_OF_TOL")
    if lst_db is not None and lst_te is not None and abs(lst_db - lst_te) > TOL_LST:
        flags.append("LST_OUT_OF_TOL")
    if _sar_mismatch(sar_db_vv, sar_db_vh, sar_te_vv, sar_te_vh):
        flags.append("SAR_OUT_OF_TOL")
    if not flags:
        return "OK" if not missing else ";".join(flags)
    return ";".join(flags)


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare test Copernicus CSV to operations.crop_indices.")
    ap.add_argument("--test-csv", type=Path, default=Path("copernicus_test_output.csv"))
    ap.add_argument("--output", type=Path, default=Path("copernicus_comparison_output.csv"))
    ap.add_argument(
        "--season-id",
        type=str,
        default=None,
        help="If set, restrict DB rows to this season_id (e.g. RABI_25_26).",
    )
    args = ap.parse_args()

    test_path = args.test_csv.resolve()
    if not test_path.is_file():
        logger.error("Test CSV not found: %s", test_path)
        return 2

    out_path = args.output.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    test_by_key = load_test_rows(test_path)
    if not test_by_key:
        logger.warning(
            "No joinable rows in test CSV (need non-empty location_id and observation_date). "
            "Writing header-only comparison to %s",
            out_path,
        )
        with out_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(OUT_FIELDNAMES), extrasaction="ignore")
            w.writeheader()
        return 0

    lids = sorted({k[0] for k in test_by_key})

    from core.db import SessionLocal

    session = SessionLocal()
    try:
        db_by_key = fetch_db_rows(session, lids, season_id=args.season_id)
    finally:
        try:
            session.rollback()
        except Exception:
            pass
        session.close()

    all_keys = sorted(set(test_by_key) | set(db_by_key))
    out_rows: list[dict[str, str]] = []

    for key in all_keys:
        lid, od = key
        tr = test_by_key.get(key)
        dr = db_by_key.get(key)

        missing: set[str] = set()
        if tr is None:
            missing.add("MISSING_TEST_ROW")
        if dr is None:
            missing.add("MISSING_DB_ROW")

        ndvi_te = _parse_float(tr.get("NDVI")) if tr else None
        evi_te = _parse_float(tr.get("EVI")) if tr else None
        lst_te = _parse_float(tr.get("LST")) if tr else None
        sar_te_vv, sar_te_vh = _parse_sar_string((tr or {}).get("SAR") or "")

        ndvi_db = float(dr["ndvi"]) if dr and dr.get("ndvi") is not None else None
        evi_db = float(dr["evi"]) if dr and dr.get("evi") is not None else None
        lst_db = float(dr["lst_celsius"]) if dr and dr.get("lst_celsius") is not None else None
        sar_db_vv = float(dr["vv_db_val"]) if dr and dr.get("vv_db_val") is not None else None
        sar_db_vh = float(dr["vh_db_val"]) if dr and dr.get("vh_db_val") is not None else None

        sar_db_str = _format_sar(sar_db_vv, sar_db_vh)
        sar_test_str = (tr or {}).get("SAR") or ""

        flag = build_mismatch_flag(
            ndvi_db=ndvi_db,
            ndvi_te=ndvi_te,
            evi_db=evi_db,
            evi_te=evi_te,
            lst_db=lst_db,
            lst_te=lst_te,
            sar_db_vv=sar_db_vv,
            sar_db_vh=sar_db_vh,
            sar_te_vv=sar_te_vv,
            sar_te_vh=sar_te_vh,
            missing=missing,
        )

        def fmt(v: Optional[float]) -> str:
            if v is None:
                return ""
            return f"{v:.6f}".rstrip("0").rstrip(".")

        out_rows.append(
            {
                "location_id": lid,
                "observation_date": od,
                "ndvi_db": fmt(ndvi_db),
                "ndvi_test": fmt(ndvi_te),
                "ndvi_diff": _diff_str(ndvi_db, ndvi_te),
                "evi_db": fmt(evi_db),
                "evi_test": fmt(evi_te),
                "evi_diff": _diff_str(evi_db, evi_te),
                "lst_db": fmt(lst_db),
                "lst_test": fmt(lst_te),
                "lst_diff": _diff_str(lst_db, lst_te),
                "sar_db": sar_db_str,
                "sar_test": sar_test_str,
                "mismatch_flag": flag,
            }
        )

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(OUT_FIELDNAMES), extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)

    logger.info("Wrote %d row(s) to %s", len(out_rows), out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
