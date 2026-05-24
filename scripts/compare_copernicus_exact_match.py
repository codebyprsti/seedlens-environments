#!/usr/bin/env python3
"""
Exact DB vs test CSV comparison for Copernicus parity checks.

Joins on ``(location_id, observation_date, file_name)``. Normalizes SAR to dB on both sides
(linear σ⁰ in DB → 10·log₁₀(σ) when values look linear).

Can be run standalone or invoked from ``test_copernicus_locations.py --comparison-output``.
"""

from __future__ import annotations

import csv
import logging
import math
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

logger = logging.getLogger(__name__)

TOL_NDVI = 1e-4
TOL_EVI = 1e-4
TOL_LST = 0.01
TOL_SAR_DB = 0.05

OUT_FIELDNAMES = (
    "location_id",
    "file_name",
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
    "sar_diff",
    "mismatch_flag",
)

_RE_VV = re.compile(r"VV_dB\s*=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", re.I)
_RE_VH = re.compile(r"VH_dB\s*=\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", re.I)


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


def _parse_sar_string(s: str) -> tuple[Optional[float], Optional[float]]:
    if not s or not str(s).strip():
        return None, None
    t = str(s).strip()
    vv_m = _RE_VV.search(t)
    vh_m = _RE_VH.search(t)
    vv = float(vv_m.group(1)) if vv_m else None
    vh = float(vh_m.group(1)) if vh_m else None
    return vv, vh


def _linear_to_db(v: float) -> float:
    x = max(float(v), 1e-12)
    return 10.0 * math.log10(x)


def _infer_db_sar_mode(vv: Optional[float], vh: Optional[float]) -> str:
    """
    Return 'linear' or 'dB' for values stored in crop_indices vv/vh columns.

    Pipeline stores dB from compute_sar_metrics; some legacy rows may hold linear σ⁰.
    """
    if vv is None and vh is None:
        return "dB"
    v = vv if vv is not None else vh
    assert v is not None
    # Typical S1 σ⁰ in dB for agriculture: roughly -25 … +5 dB
    if v < -4.0 or v > 10.0:
        return "dB"
    if 0.0 <= v <= 1.5:
        return "linear"
    return "dB"


def _sar_pair_to_db(vv: Optional[float], vh: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    mode = _infer_db_sar_mode(vv, vh)
    if mode == "linear":
        return (
            _linear_to_db(vv) if vv is not None and vv > 0 else None,
            _linear_to_db(vh) if vh is not None and vh > 0 else None,
        )
    return vv, vh


def _format_sar_db(vv: Optional[float], vh: Optional[float]) -> str:
    if vv is None and vh is None:
        return ""
    parts = []
    if vv is not None:
        parts.append(f"VV_dB={vv:.4f}")
    if vh is not None:
        parts.append(f"VH_dB={vh:.4f}")
    return ";".join(parts)


def _sar_diff_summary(
    vv_db: Optional[float],
    vh_db: Optional[float],
    vv_te: Optional[float],
    vh_te: Optional[float],
) -> str:
    dv = (
        abs(vv_db - vv_te)
        if vv_db is not None and vv_te is not None
        else None
    )
    dh = (
        abs(vh_db - vh_te)
        if vh_db is not None and vh_te is not None
        else None
    )
    parts = []
    if dv is not None:
        parts.append(f"max_dVV_dB={dv:.4f}")
    if dh is not None:
        parts.append(f"max_dVH_dB={dh:.4f}")
    return ";".join(parts) if parts else ""


def _crop_indices_sar_column_names(session) -> tuple[str, str]:
    from sqlalchemy import text

    rows = session.execute(
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


def _obs_date_column(session) -> str:
    from crop_monitoring.insert_validator import crop_indices_observation_date_column

    return crop_indices_observation_date_column(session)


def load_test_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_db_row_exact(
    session,
    *,
    location_id: str,
    file_name: str,
    observation_date: str,
    dcol: str,
    vv_c: str,
    vh_c: str,
    season_id: Optional[str],
) -> Optional[dict[str, Any]]:
    from sqlalchemy import text

    od = observation_date.strip()[:10]
    season_sql = ""
    params: dict[str, Any] = {"lid": location_id, "fn": file_name, "od": od}
    if season_id:
        season_sql = " AND season_id = :season_id "
        params["season_id"] = season_id

    q = text(
        f"""
        SELECT
            ndvi, evi, lst_celsius,
            {vv_c} AS vv_raw,
            {vh_c} AS vh_raw
        FROM operations.crop_indices
        WHERE location_id = :lid
          AND file_name = :fn
          AND ({dcol}::date) = CAST(:od AS date)
        {season_sql}
        ORDER BY id DESC
        LIMIT 1
        """
    )
    row = session.execute(q, params).mappings().first()
    return dict(row) if row else None


def _diff_str(a: Optional[float], b: Optional[float]) -> str:
    if a is None or b is None:
        return ""
    return f"{float(a) - float(b):.8f}".rstrip("0").rstrip(".")


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return ""
    return f"{float(v):.8f}".rstrip("0").rstrip(".")


def build_mismatch_flag(
    *,
    ndvi_db: Optional[float],
    ndvi_te: Optional[float],
    evi_db: Optional[float],
    evi_te: Optional[float],
    lst_db: Optional[float],
    lst_te: Optional[float],
    vv_d: Optional[float],
    vh_d: Optional[float],
    vv_t: Optional[float],
    vh_t: Optional[float],
    missing_db: bool,
) -> str:
    flags: list[str] = []
    if missing_db:
        flags.append("MISSING_DB_ROW")
    if ndvi_db is not None and ndvi_te is not None and abs(ndvi_db - ndvi_te) > TOL_NDVI:
        flags.append("NDVI_MISMATCH")
    if evi_db is not None and evi_te is not None and abs(evi_db - evi_te) > TOL_EVI:
        flags.append("EVI_MISMATCH")
    if lst_db is not None and lst_te is not None and abs(lst_db - lst_te) > TOL_LST:
        flags.append("LST_MISMATCH")
    if vv_d is not None and vv_t is not None and abs(vv_d - vv_t) > TOL_SAR_DB:
        flags.append("SAR_VV_MISMATCH")
    if vh_d is not None and vh_t is not None and abs(vh_d - vh_t) > TOL_SAR_DB:
        flags.append("SAR_VH_MISMATCH")
    if not flags:
        return "OK"
    return ";".join(flags)


def write_exact_match_comparison(
    *,
    test_csv_path: Path,
    output_path: Path,
    season_id: Optional[str] = None,
) -> int:
    """
    Read test CSV (must include file_name, observation_date), join DB on triple key, write comparison CSV.
    Returns number of data rows written.
    """
    from core.db import SessionLocal

    test_path = test_csv_path.resolve()
    if not test_path.is_file():
        raise FileNotFoundError(str(test_path))

    rows_in = load_test_csv(test_path)
    out_path = output_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    session = SessionLocal()
    out_rows: list[dict[str, str]] = []
    try:
        dcol = _obs_date_column(session)
        vv_c, vh_c = _crop_indices_sar_column_names(session)

        for tr in rows_in:
            lid = (tr.get("location_id") or "").strip()
            fn = (tr.get("file_name") or "").strip()
            od = (tr.get("observation_date") or "").strip()[:10]
            if not lid or not fn or not od:
                logger.debug("skip row (missing location_id/file_name/observation_date): %r", tr)
                continue

            dr = fetch_db_row_exact(
                session,
                location_id=lid,
                file_name=fn,
                observation_date=od,
                dcol=dcol,
                vv_c=vv_c,
                vh_c=vh_c,
                season_id=season_id,
            )

            ndvi_te = _parse_float(tr.get("NDVI"))
            evi_te = _parse_float(tr.get("EVI"))
            lst_te = _parse_float(tr.get("LST"))
            vv_te, vh_te = _parse_sar_string(tr.get("SAR") or "")

            missing_db = dr is None
            ndvi_db = _parse_float(dr.get("ndvi")) if dr else None
            evi_db = _parse_float(dr.get("evi")) if dr else None
            lst_db = _parse_float(dr.get("lst_celsius")) if dr else None
            vv_raw = _parse_float(dr.get("vv_raw")) if dr else None
            vh_raw = _parse_float(dr.get("vh_raw")) if dr else None

            vv_db_d, vh_db_d = _sar_pair_to_db(vv_raw, vh_raw)
            sar_db_str = _format_sar_db(vv_db_d, vh_db_d)
            sar_test_str = (tr.get("SAR") or "").strip()
            sar_diff = _sar_diff_summary(vv_db_d, vh_db_d, vv_te, vh_te)

            flag = build_mismatch_flag(
                ndvi_db=ndvi_db,
                ndvi_te=ndvi_te,
                evi_db=evi_db,
                evi_te=evi_te,
                lst_db=lst_db,
                lst_te=lst_te,
                vv_d=vv_db_d,
                vh_d=vh_db_d,
                vv_t=vv_te,
                vh_t=vh_te,
                missing_db=missing_db,
            )

            out_rows.append(
                {
                    "location_id": lid,
                    "file_name": fn,
                    "observation_date": od,
                    "ndvi_db": _fmt(ndvi_db),
                    "ndvi_test": _fmt(ndvi_te),
                    "ndvi_diff": _diff_str(ndvi_db, ndvi_te),
                    "evi_db": _fmt(evi_db),
                    "evi_test": _fmt(evi_te),
                    "evi_diff": _diff_str(evi_db, evi_te),
                    "lst_db": _fmt(lst_db),
                    "lst_test": _fmt(lst_te),
                    "lst_diff": _diff_str(lst_db, lst_te),
                    "sar_db": sar_db_str,
                    "sar_test": sar_test_str,
                    "sar_diff": sar_diff,
                    "mismatch_flag": flag,
                }
            )
    finally:
        try:
            session.rollback()
        except Exception:
            pass
        session.close()

    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(OUT_FIELDNAMES), extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)

    logger.info("Wrote %d comparison row(s) to %s", len(out_rows), out_path)
    return len(out_rows)


def main() -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="Compare KML-aligned test CSV to operations.crop_indices.")
    p.add_argument("--test-csv", type=Path, default=Path("copernicus_test_output.csv"))
    p.add_argument("--output", type=Path, default=Path("copernicus_exact_match_comparison.csv"))
    p.add_argument("--season-id", type=str, default=None)
    args = p.parse_args()
    write_exact_match_comparison(
        test_csv_path=args.test_csv,
        output_path=args.output,
        season_id=args.season_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
