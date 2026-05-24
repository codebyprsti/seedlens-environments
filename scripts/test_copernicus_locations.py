#!/usr/bin/env python3
"""
Read-only Copernicus / Sentinel Hub test aligned with ``run_crop_analysis_s3_batch.py`` geometry.

For each ``(location_id, file_name)`` from ``operations.crop_indices``, loads the same KML used at
ingest (S3 or local), runs ``parse_kml`` → full parcel GeoJSON, then the same Statistical API +
``_lst_sar_polygon_means_for_analysis_date`` path as the batch (no DB writes).

Usage:
  python scripts/test_copernicus_locations.py --start 2025-12-01 --end 2026-03-18 --season-id RABI_25_26 \\
    --kml-source s3
  python scripts/test_copernicus_locations.py --start 2025-12-01 --end 2026-03-18 --season-id RABI_25_26 \\
    --kml-source local --local-kml-dir /path/to/kml

Requires: DB read (``operations.crop_indices``), Sentinel Hub credentials, and for ``s3`` mode
AWS credentials (same Secrets Manager pattern as the batch script).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import shutil
import logging
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
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

try:
    from crop_monitoring.sh_http_setup import configure_sh_http

    configure_sh_http()
except Exception as e:
    print(f"Warning: could not apply Copernicus TLS setup: {e}", file=sys.stderr)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_LOCATION_IDS = (
    "IND-KA-600682",
    "IND-KA-601215",
    "IND-KA-601190",
    "IND-KA-600564",
    "IND-KA-600070",
)

CSV_FIELDNAMES = (
    "location_id",
    "file_name",
    "observation_date",
    "NDVI",
    "EVI",
    "NDMI",
    "LST",
    "SAR",
    "polygon_area_ha_kml",
    "polygon_area_ha_db",
    "geometry_sha256",
    "error_message",
)


def _load_batch_helpers():
    """Load batch helpers (LST/SAR, polygon area, S3 helpers) without forking logic."""
    batch_path = _root / "scripts" / "run_crop_analysis_s3_batch.py"
    spec = importlib.util.spec_from_file_location("run_crop_analysis_s3_batch", batch_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load batch module from {batch_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _geojson_fingerprint(geojson: dict) -> str:
    """Stable digest of geometry JSON (ordering normalized) for logging."""
    try:
        blob = json.dumps(geojson, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        blob = str(geojson)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _format_sar(vv_db: Optional[float], vh_db: Optional[float]) -> str:
    if vv_db is None and vh_db is None:
        return ""
    parts = []
    if vv_db is not None:
        parts.append(f"VV_dB={vv_db:.4f}")
    if vh_db is not None:
        parts.append(f"VH_dB={vh_db:.4f}")
    return ";".join(parts)


def fetch_distinct_location_files(
    session,
    location_ids: list[str],
    *,
    season_id: Optional[str] = None,
) -> list[tuple[str, str]]:
    """Return sorted distinct (location_id, file_name) from crop_indices."""
    from sqlalchemy import bindparam, text

    season_sql = ""
    params: dict[str, Any] = {"lids": location_ids}
    if season_id:
        season_sql = " AND season_id = :season_id "
        params["season_id"] = season_id

    q = (
        text(
            f"""
            SELECT DISTINCT location_id, file_name
            FROM operations.crop_indices
            WHERE location_id IN :lids
              AND file_name IS NOT NULL
              AND TRIM(file_name) <> ''
            {season_sql}
            ORDER BY location_id, file_name
            """
        ).bindparams(bindparam("lids", expanding=True))
    )
    rows = session.execute(q, params).fetchall()
    out: list[tuple[str, str]] = []
    for r in rows:
        if not r or len(r) < 2:
            continue
        lid, fn = str(r[0]).strip(), str(r[1]).strip()
        if lid and fn:
            out.append((lid, fn))
    return out


def fetch_polygon_area_ha_db(session, location_id: str, file_name: str, *, season_id: Optional[str]) -> Optional[float]:
    from sqlalchemy import text

    season_sql = ""
    params: dict[str, Any] = {"lid": location_id, "fn": file_name}
    if season_id:
        season_sql = " AND season_id = :season_id "
        params["season_id"] = season_id
    try:
        row = session.execute(
            text(
                f"""
                SELECT polygon_area
                FROM operations.crop_indices
                WHERE location_id = :lid AND file_name = :fn
                {season_sql}
                ORDER BY polygon_area NULLS LAST
                LIMIT 1
                """
            ),
            params,
        ).fetchone()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return None


def _normalize_name(s: str) -> str:
    return (s or "").strip().replace("+", " ")


def resolve_s3_key_for_file_name(
    batch_mod: Any,
    s3_client: Any,
    file_name: str,
    *,
    primary_prefix: str,
    fallback_prefixes: tuple[str, ...],
) -> Optional[str]:
    """
    Map DB ``file_name`` (basename) to full S3 object key.

    Searches ``primary_prefix`` first, then each fallback (e.g. whole ``input_files`` tree)
    with paginated listing — stops at first basename match.
    """
    bucket = batch_mod.S3_BUCKET
    want = _normalize_name(Path(file_name).name)
    ordered = (primary_prefix,) + fallback_prefixes
    seen: set[str] = set()
    for raw in ordered:
        p = (raw or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        pfx = p.rstrip("/") + "/"
        paginator = s3_client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=bucket, Prefix=pfx):
                for obj in page.get("Contents") or []:
                    k = (obj.get("Key") or "").strip()
                    if not k.endswith(".kml") or (obj.get("Size") or 0) <= 0:
                        continue
                    if _normalize_name(Path(k).name) == want:
                        logger.info("Resolved S3 key for %r -> %s (under prefix %s)", file_name, k, pfx)
                        return k
        except Exception as e:
            logger.warning("S3 list failed under prefix %r: %s", pfx, e)
    return None


def resolve_local_kml_path(local_dir: Path, file_name: str) -> Optional[Path]:
    """Find ``*.kml`` under ``local_dir`` whose basename matches ``file_name``."""
    want = Path(file_name).name
    root = local_dir.resolve()
    if not root.is_dir():
        return None
    for p in sorted(root.rglob("*.kml"), key=lambda x: str(x).lower()):
        if p.name == want or _normalize_name(p.name) == _normalize_name(want):
            return p
    return None


def load_kml_geojson(
    *,
    kml_source: str,
    file_name: str,
    batch_mod: Any,
    local_dir: Optional[Path],
    s3_prefix: str,
    s3_fallback_prefixes: tuple[str, ...],
) -> tuple[Optional[dict[str, Any]], Optional[Path], str]:
    """
    Return (geojson_dict, local_path_used, error_message).
    """
    from crop_monitoring.kml_parser import parse_kml

    if kml_source == "local":
        if not local_dir:
            return None, None, "--local-kml-dir is required when --kml-source local"
        lp = resolve_local_kml_path(local_dir, file_name)
        if not lp or not lp.is_file():
            return None, None, f"KML not found under local dir for file_name={file_name!r}"
        try:
            geojson, _meta = parse_kml(lp)
            return geojson, lp, ""
        except Exception as e:
            return None, lp, f"parse_kml failed: {e}"

    if kml_source != "s3":
        return None, None, f"unknown --kml-source {kml_source!r}"

    try:
        import boto3

        creds = batch_mod.get_aws_credentials()
        s3_client = boto3.client(
            "s3",
            region_name=batch_mod.AWS_REGION,
            aws_access_key_id=creds["aws_access_key_id"],
            aws_secret_access_key=creds["aws_secret_access_key"],
        )
    except Exception as e:
        return None, None, f"AWS / S3 client failed: {e}"

    key = resolve_s3_key_for_file_name(
        batch_mod,
        s3_client,
        file_name,
        primary_prefix=s3_prefix,
        fallback_prefixes=s3_fallback_prefixes,
    )
    if not key and "/" in file_name.strip():
        key = file_name.strip()

    if not key:
        return None, None, f"No S3 key found for file_name={file_name!r} under prefix={s3_prefix!r}"

    tmpd = Path(tempfile.mkdtemp(prefix="copernicus_test_kml_"))
    local_path = tmpd / Path(key).name
    try:
        batch_mod.download_kml(s3_client, key, local_path)
        geojson, _meta = parse_kml(local_path)
        return geojson, local_path, ""
    except Exception as e:
        return None, local_path, f"S3 download or parse_kml failed: {e}"
    finally:
        try:
            shutil.rmtree(tmpd, ignore_errors=True)
        except Exception:
            pass


DEFAULT_S3_FALLBACK_PREFIXES = ("seedworks/kml_files/input_files/",)


def process_one_location_file(
    session,
    location_id: str,
    file_name: str,
    start_str: str,
    end_str: str,
    lst_sar_fn: Any,
    polygon_area_fn: Any,
    batch_mod: Any,
    *,
    kml_source: str,
    local_dir: Optional[Path],
    s3_prefix: str,
    s3_fallback_prefixes: tuple[str, ...],
    season_id: Optional[str],
    maxcc: float = 20.0,
    lst_sar_workers: int = 8,
) -> list[dict[str, Any]]:
    from crop_monitoring.statistical_client import fetch_s2_indices_timeseries

    geojson, kml_path, kml_err = load_kml_geojson(
        kml_source=kml_source,
        file_name=file_name,
        batch_mod=batch_mod,
        local_dir=local_dir,
        s3_prefix=s3_prefix,
        s3_fallback_prefixes=s3_fallback_prefixes,
    )
    base_row = {
        "location_id": location_id,
        "file_name": file_name,
        "observation_date": "",
        "NDVI": "",
        "EVI": "",
        "NDMI": "",
        "LST": "",
        "SAR": "",
        "polygon_area_ha_kml": "",
        "polygon_area_ha_db": "",
        "geometry_sha256": "",
        "error_message": kml_err,
    }
    if kml_err or not geojson:
        logger.error("location_id=%s file_name=%s: %s", location_id, file_name, kml_err)
        return [base_row]

    area_kml = polygon_area_fn(geojson)
    area_db = fetch_polygon_area_ha_db(session, location_id, file_name, season_id=season_id)
    fp = _geojson_fingerprint(geojson)

    logger.info(
        "Geometry audit location_id=%s file_name=%s geometry_sha256=%s polygon_area_ha_kml=%.6f polygon_area_ha_db=%s",
        location_id,
        file_name,
        fp,
        float(area_kml) if area_kml == area_kml else float("nan"),
        f"{area_db:.6f}" if area_db is not None else "NULL",
    )
    if area_db is not None and area_kml == area_kml and area_db > 0:
        rel = abs(float(area_kml) - float(area_db)) / float(area_db)
        if rel > 0.02:
            logger.warning(
                "Polygon area mismatch (>2%%): kml=%.6f ha db=%.6f ha relative_diff=%.4f (different geometry or stale DB polygon_area)",
                float(area_kml),
                float(area_db),
                rel,
            )
        else:
            logger.info("Polygon area check OK: relative_diff=%.6f", rel)

    rows_out: list[dict[str, Any]] = []

    try:
        rows_idx = fetch_s2_indices_timeseries(geojson, start_str, end_str, maxcc=maxcc)
    except Exception as e:
        msg = f"Statistical API failed: {e}"
        logger.exception("location_id=%s file_name=%s %s", location_id, file_name, msg)
        return [
            {
                **base_row,
                "polygon_area_ha_kml": f"{area_kml:.6f}" if area_kml == area_kml else "",
                "polygon_area_ha_db": f"{area_db:.6f}" if area_db is not None else "",
                "geometry_sha256": fp,
                "error_message": msg,
            }
        ]

    if not rows_idx:
        msg = "Statistical API returned no daily intervals (empty or all masked)"
        logger.warning("location_id=%s file_name=%s: %s", location_id, file_name, msg)
        return [
            {
                **base_row,
                "polygon_area_ha_kml": f"{area_kml:.6f}" if area_kml == area_kml else "",
                "polygon_area_ha_db": f"{area_db:.6f}" if area_db is not None else "",
                "geometry_sha256": fp,
                "error_message": msg,
            }
        ]

    def _norm_day(ad: Any) -> str:
        s = str(ad).strip()
        return s[:10] if len(s) >= 10 else s

    unique_dates: list[str] = []
    seen_d: set[str] = set()
    for obs in rows_idx:
        ad = obs.get("analysis_date")
        if not ad:
            continue
        d = _norm_day(ad)
        if d and d not in seen_d:
            seen_d.add(d)
            unique_dates.append(d)

    day_metrics: dict[str, tuple[Optional[float], Optional[float], Optional[float], Optional[float]]] = {}
    day_errors: dict[str, str] = {}

    def _fetch_lst_sar(day: str) -> tuple[str, tuple[Optional[float], Optional[float], Optional[float], Optional[float]], str]:
        try:
            return day, lst_sar_fn(geojson, day), ""
        except Exception as e:
            return day, (None, None, None, None), str(e)

    _nw = min(max(1, lst_sar_workers), max(1, len(unique_dates)))
    if unique_dates:
        with ThreadPoolExecutor(max_workers=_nw) as _ex:
            for fut in as_completed([_ex.submit(_fetch_lst_sar, ad) for ad in unique_dates]):
                ad, tup, err = fut.result()
                day_metrics[ad] = tup
                if err:
                    day_errors[ad] = err

    for obs in rows_idx:
        ad = obs.get("analysis_date")
        if not ad:
            continue
        day = _norm_day(ad)
        ndvi = obs.get("NDVI")
        evi = obs.get("EVI")
        ndmi = obs.get("NDMI")
        if ndvi is None and evi is None and ndmi is None:
            logger.info(
                "location_id=%s file_name=%s date=%s: index means all null",
                location_id,
                file_name,
                day,
            )

        lst_c, vv_db, vh_db, _rat = day_metrics.get(day, (None, None, None, None))
        lst_val = lst_c
        sar_str = _format_sar(vv_db, vh_db)
        row_err = day_errors.get(day, "")

        def _fmt_num(v: Any) -> str:
            if v is None:
                return ""
            try:
                return f"{float(v):.6f}".rstrip("0").rstrip(".")
            except (TypeError, ValueError):
                return str(v)

        rows_out.append(
            {
                "location_id": location_id,
                "file_name": file_name,
                "observation_date": day,
                "NDVI": _fmt_num(ndvi),
                "EVI": _fmt_num(evi),
                "NDMI": _fmt_num(ndmi),
                "LST": _fmt_num(lst_val) if lst_val is not None else "",
                "SAR": sar_str,
                "polygon_area_ha_kml": f"{area_kml:.6f}" if area_kml == area_kml else "",
                "polygon_area_ha_db": f"{area_db:.6f}" if area_db is not None else "",
                "geometry_sha256": fp,
                "error_message": row_err,
            }
        )

    return rows_out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Read-only Copernicus test: same KML polygon as crop_indices ingest → CSV."
    )
    p.add_argument("--start", required=True, metavar="YYYY-MM-DD", help="Interval start (inclusive)")
    p.add_argument("--end", required=True, metavar="YYYY-MM-DD", help="Interval end (inclusive)")
    p.add_argument(
        "--location-ids",
        type=str,
        default=",".join(DEFAULT_LOCATION_IDS),
        help=f"Comma-separated location_id values (default: {len(DEFAULT_LOCATION_IDS)} test IDs)",
    )
    p.add_argument(
        "--season-id",
        type=str,
        default=None,
        help="Restrict crop_indices (location_id, file_name) pairs to this season_id (recommended).",
    )
    p.add_argument(
        "--kml-source",
        choices=("s3", "local"),
        default="s3",
        help="Where to load KML files (default: s3, same bucket/prefix pattern as batch).",
    )
    p.add_argument(
        "--local-kml-dir",
        type=Path,
        default=None,
        help="Recursive search root for *.kml when --kml-source local.",
    )
    p.add_argument(
        "--s3-prefix",
        type=str,
        default=None,
        help="Primary S3 prefix to search first (default: batch script S3_PREFIX).",
    )
    p.add_argument(
        "--s3-fallback-prefix",
        action="append",
        default=None,
        metavar="PREFIX",
        help="Extra S3 prefix to search for KML by basename (after --s3-prefix). "
        "Repeatable. If omitted, uses seedworks/kml_files/input_files/",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("copernicus_test_output.csv"),
        help="Output CSV path (default: ./copernicus_test_output.csv)",
    )
    p.add_argument(
        "--comparison-output",
        type=Path,
        default=None,
        help="If set, write copernicus_exact_match_comparison.csv to this path after the test run.",
    )
    p.add_argument(
        "--maxcc",
        type=float,
        default=20.0,
        help="Max cloud cover %% for Statistical API (default: 20)",
    )
    p.add_argument(
        "--lst-sar-workers",
        type=int,
        default=8,
        metavar="N",
        help="Parallel workers for per-day LST/S1 calls (default: 8).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        date.fromisoformat(args.start[:10])
        date.fromisoformat(args.end[:10])
    except ValueError:
        logger.error("Invalid --start or --end date (expected YYYY-MM-DD)")
        return 2

    location_ids = [x.strip() for x in str(args.location_ids).split(",") if x.strip()]
    if not location_ids:
        logger.error("No location IDs after parsing --location-ids")
        return 2

    if args.kml_source == "local" and not args.local_kml_dir:
        logger.error("--local-kml-dir is required when --kml-source local")
        return 2

    batch_mod = _load_batch_helpers()
    lst_sar_fn = batch_mod._lst_sar_polygon_means_for_analysis_date
    polygon_area_fn = batch_mod._polygon_area_ha
    s3_prefix = (args.s3_prefix or batch_mod.S3_PREFIX).rstrip("/").strip()
    if args.s3_fallback_prefix:
        s3_fallbacks = tuple(p.strip().rstrip("/") for p in args.s3_fallback_prefix if p and str(p).strip())
    else:
        s3_fallbacks = DEFAULT_S3_FALLBACK_PREFIXES

    from core.db import SessionLocal

    all_rows: list[dict[str, Any]] = []
    session = SessionLocal()
    try:
        pairs = fetch_distinct_location_files(session, location_ids, season_id=args.season_id)
        if not pairs:
            logger.error(
                "No (location_id, file_name) rows in operations.crop_indices for given IDs / season."
            )
            return 2
        logger.info("Resolved %d distinct (location_id, file_name) pair(s) from DB", len(pairs))
        for lid, fn in pairs:
            logger.info("Processing location_id=%s file_name=%s", lid, fn)
            all_rows.extend(
                process_one_location_file(
                    session,
                    lid,
                    fn,
                    args.start,
                    args.end,
                    lst_sar_fn,
                    polygon_area_fn,
                    batch_mod,
                    kml_source=args.kml_source,
                    local_dir=args.local_kml_dir,
                    s3_prefix=s3_prefix,
                    s3_fallback_prefixes=s3_fallbacks,
                    season_id=args.season_id,
                    maxcc=args.maxcc,
                    lst_sar_workers=args.lst_sar_workers,
                )
            )
    finally:
        try:
            session.rollback()
        except Exception:
            pass
        session.close()

    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(CSV_FIELDNAMES), extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    logger.info("Wrote %d row(s) to %s", len(all_rows), out_path)

    if args.comparison_output:
        try:
            cmp_py = _root / "scripts" / "compare_copernicus_exact_match.py"
            spec = importlib.util.spec_from_file_location("compare_copernicus_exact_match", cmp_py)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Cannot load comparison module from {cmp_py}")
            cmp_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cmp_mod)
            cmp_path = Path(args.comparison_output).resolve()
            n = cmp_mod.write_exact_match_comparison(
                test_csv_path=out_path,
                output_path=cmp_path,
                season_id=args.season_id,
            )
            logger.info("Wrote %d comparison row(s) to %s", n, cmp_path)
        except Exception as e:
            logger.exception("Comparison export failed: %s", e)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
