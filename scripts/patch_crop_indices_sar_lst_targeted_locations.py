#!/usr/bin/env python3
"""
Recompute SAR (as linear VV/VH) and LST for specific crop_indices rows only.

Use when vv/vh/lst_celsius are incorrectly constant over time (bad backfill, etc.).

- Reads rows from configurable schema.table (default: crop_intel.crop_indices).
- Geometry: prefers operations.location_polygons.polygon_geojson for the location_id;
  otherwise downloads/parses the KML referenced by file_name (S3 + local fallback).
- Reuses _lst_sar_polygon_means_for_analysis_date (same sliding windows as batch pipeline).
- Writes linear SAR: VV = 10**(vv_db/10), VH = 10**(vh_db/10); lst_celsius unchanged scale.
- Transaction: one DB commit per batch of rows (rollback on failure for that batch).

Example (production — review --dry-run first):

  python scripts/patch_crop_indices_sar_lst_targeted_locations.py \\
    --indices-schema crop_intel --indices-table crop_indices \\
    --date-column index_date \\
    --sar-vv-col VV --sar-vh-col VH \\
    --build-s3-index \\
    --s3-prefix seedworks/kml_files/input_files/OD/OD/ \\
    --s3-prefix seedworks/kml_files/input_files/AP/AP/ \\
    --s3-prefix seedworks/kml_files/input_files/WB/WB/

Validation SQL (after run):

  SELECT location_id,
         COUNT(DISTINCT "VV") AS dv,
         COUNT(DISTINCT "VH") AS dh,
         COUNT(DISTINCT lst_celsius) AS dl
  FROM crop_intel.crop_indices
  WHERE location_id IN (...)
  GROUP BY location_id;
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _install_sh_rate_limit_log_suppression() -> None:
    _orig = warnings.showwarning

    def _showwarning(message, category, filename, lineno, file=None, line=None):
        try:
            cat_name = getattr(category, "__name__", "") or ""
            if cat_name == "SHRateLimitWarning":
                return
            if "rate limit hit" in str(message).lower():
                return
        except Exception:
            pass
        return _orig(message, category, filename, lineno, file=file, line=line)

    warnings.showwarning = _showwarning
    try:
        from sentinelhub.exceptions import SHRateLimitWarning

        warnings.filterwarnings("ignore", category=SHRateLimitWarning)
    except ImportError:
        pass


_install_sh_rate_limit_log_suppression()

from core.db import SessionLocal  # noqa: E402
from scripts.run_crop_analysis_s3_batch import (  # noqa: E402
    S3_BUCKET,
    _lst_sar_polygon_means_for_analysis_date,
)

logger = logging.getLogger(__name__)

DEFAULT_LOCATION_IDS = [
    "IND-OD-603821",
    "IND-AP-600000",
    "IND-OD-606299",
    "IND-OD-606274",
    "IND-OD-603877",
    "IND-AP-600005",
    "IND-OD-606542",
    "IND-OD-606549",
    "IND-WB-600908",
    "IND-OD-606577",
]

_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$", re.I)


def _quote_ident(name: str) -> str:
    """PostgreSQL identifier; use double quotes for mixed case (e.g. VV)."""
    if _SAFE_IDENT.match(name) and name.lower() not in ("user", "order"):
        return name
    return '"' + name.replace('"', '""') + '"'


def _db_to_linear(db_val: Optional[float]) -> Optional[float]:
    if db_val is None or not math.isfinite(float(db_val)):
        return None
    return float(10 ** (float(db_val) / 10.0))


def _geojson_from_polygon_row(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _detect_date_column(db, *, schema: str, table: str) -> str:
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
              AND column_name IN ('index_date', 'analysis_date')
            """
        ),
        {"schema": schema, "table": table},
    ).fetchall()
    names = {str(r[0]).lower() for r in rows}
    if "index_date" in names:
        return "index_date"
    if "analysis_date" in names:
        return "analysis_date"
    raise RuntimeError(
        f"No index_date/analysis_date on {schema}.{table}; pass --date-column explicitly."
    )


def _column_exists(db, *, schema: str, table: str, column: str) -> bool:
    n = db.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table AND column_name = :col
            """
        ),
        {"schema": schema, "table": table, "col": column},
    ).fetchone()
    return n is not None


def _centroid_square_geojson(lat: float, lon: float, half_size_deg: float = 0.00045) -> dict[str, Any]:
    min_lon = float(lon) - half_size_deg
    max_lon = float(lon) + half_size_deg
    min_lat = float(lat) - half_size_deg
    max_lat = float(lat) + half_size_deg
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [min_lon, min_lat],
                [max_lon, min_lat],
                [max_lon, max_lat],
                [min_lon, max_lat],
                [min_lon, min_lat],
            ]
        ],
    }


def _s3_client():
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError("boto3 required for S3 KML fetch") from e
    aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    aws_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
    if aws_access_key_id and aws_secret_access_key:
        return boto3.client(
            "s3",
            region_name=aws_region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )
    from scripts.run_crop_analysis_s3_batch import AWS_REGION, get_aws_credentials

    creds = get_aws_credentials()
    return boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=creds["aws_access_key_id"],
        aws_secret_access_key=creds["aws_secret_access_key"],
    )


def _build_s3_basename_index(s3_client, *, bucket: str, prefixes: list[str]) -> dict[str, str]:
    try:
        from crop_monitoring.s3_file_loader import list_kml_files
    except ImportError:
        list_kml_files = None

    index: dict[str, str] = {}
    for p in prefixes:
        p2 = (p or "").strip()
        if not p2:
            continue
        if list_kml_files:
            keys = list_kml_files(s3_client, bucket, p2, skip_empty=True)
        else:
            import boto3

            paginator = s3_client.get_paginator("list_objects_v2")
            keys = []
            pref = p2.rstrip("/") + "/"
            for page in paginator.paginate(Bucket=bucket, Prefix=pref):
                for obj in page.get("Contents") or []:
                    k = (obj.get("Key") or "").strip()
                    if k.endswith(".kml") and (obj.get("size") or obj.get("Size") or 0) > 0:
                        keys.append(k)
        for k in keys:
            base = Path(k).name
            index.setdefault(base, k)
    return index


def _fetch_kml_to_path(
    *,
    file_name: str,
    out_path: Path,
    s3_client: Any,
    bucket: str,
    prefixes: list[str],
    basename_index: dict[str, str] | None,
    local_root: Optional[Path],
) -> bool:
    if s3_client is not None:
        if basename_index:
            key = basename_index.get(Path(file_name).name)
            if key:
                s3_client.download_file(bucket, key, str(out_path))
                return True
        for p in prefixes:
            p2 = (p or "").strip()
            if p2 and not p2.endswith("/"):
                p2 += "/"
            key = f"{p2}{Path(file_name).name}"
            try:
                s3_client.head_object(Bucket=bucket, Key=key)
                s3_client.download_file(bucket, key, str(out_path))
                return True
            except Exception:
                continue
    if local_root and local_root.is_dir():
        target_base = Path(file_name).name
        direct = local_root / target_base
        if direct.is_file():
            shutil.copyfile(direct, out_path)
            return True
        matches = [m for m in local_root.rglob(target_base) if m.is_file()]
        if matches:
            matches.sort(key=lambda p: (len(str(p)), str(p).lower()))
            shutil.copyfile(matches[0], out_path)
            return True
    return False


def _resolve_geojson_for_location(
    db,
    *,
    location_id: str,
    file_name: str,
    polygon_schema: str,
    field_locations_schema: str,
    s3_client: Any,
    s3_bucket: str,
    s3_prefixes: list[str],
    basename_index: dict[str, str] | None,
    local_root: Optional[Path],
    tmp_dir: Path,
) -> dict[str, Any]:
    pq = text(
        f"""
        SELECT polygon_geojson
        FROM {_quote_ident(polygon_schema)}.location_polygons
        WHERE location_id = :lid AND polygon_index = 0
        LIMIT 1
        """
    )
    row = db.execute(pq, {"lid": location_id}).fetchone()
    if row and row[0]:
        gj = _geojson_from_polygon_row(row[0])
        if gj:
            logger.info("Using DB polygon for %s", location_id)
            return gj

    flq = text(
        f"""
        SELECT latitude, longitude
        FROM {_quote_ident(field_locations_schema)}.field_locations
        WHERE location_id = :lid
        LIMIT 1
        """
    )
    fl = db.execute(flq, {"lid": location_id}).fetchone()
    local_kml = tmp_dir / f"{location_id}_{Path(file_name).name}"
    s3c = s3_client
    if s3c is None and (s3_prefixes or basename_index):
        s3c = _s3_client()
    if _fetch_kml_to_path(
        file_name=file_name,
        out_path=local_kml,
        s3_client=s3c,
        bucket=s3_bucket,
        prefixes=s3_prefixes,
        basename_index=basename_index,
        local_root=local_root,
    ):
        from crop_monitoring.kml_parser import parse_kml

        geojson, _meta = parse_kml(local_kml)
        logger.info("Using KML polygon for %s (%s)", location_id, file_name)
        return geojson

    if fl:
        lat, lon = float(fl[0]), float(fl[1])
        logger.warning(
            "No polygon/KML for %s — fallback centroid square (~100 m); LST/SAR may differ from full-field pipeline.",
            location_id,
        )
        return _centroid_square_geojson(lat, lon)

    raise RuntimeError(f"No geometry for location_id={location_id} (no polygon, KML, or field_locations).")


def _fetch_rows(
    db,
    *,
    indices_schema: str,
    indices_table: str,
    date_col: str,
    location_ids: list[str],
) -> list[dict[str, Any]]:
    if not location_ids:
        return []
    placeholders = ",".join(f":id{i}" for i in range(len(location_ids)))
    params = {f"id{i}": lid for i, lid in enumerate(location_ids)}
    q = text(
        f"""
        SELECT id, location_id, file_name, {_quote_ident(date_col)}::text AS obs_date
        FROM {_quote_ident(indices_schema)}.{_quote_ident(indices_table)}
        WHERE location_id IN ({placeholders})
          AND file_name IS NOT NULL
        ORDER BY location_id, {_quote_ident(date_col)}, id
        """
    )
    return [dict(r) for r in db.execute(q, params).mappings().all()]


def _build_update_sql(
    *,
    indices_schema: str,
    indices_table: str,
    sar_vv_col: str,
    sar_vh_col: str,
    set_vh_vv: bool,
    set_lst: bool,
) -> str:
    parts = [
        f"{_quote_ident(sar_vv_col)} = :vv",
        f"{_quote_ident(sar_vh_col)} = :vh",
    ]
    if set_vh_vv:
        parts.append("vh_vv_ratio = :vh_vv_ratio")
    if set_lst:
        parts.append("lst_celsius = :lst_celsius")
    sets = ", ".join(parts)
    return f'UPDATE {_quote_ident(indices_schema)}.{_quote_ident(indices_table)} SET {sets} WHERE id = :id'


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    p = argparse.ArgumentParser(description="Patch SAR/LST for specific location_ids in crop_indices.")
    p.add_argument(
        "--location-ids",
        type=str,
        default=",".join(DEFAULT_LOCATION_IDS),
        help="Comma-separated location_ids",
    )
    p.add_argument("--indices-schema", type=str, default="crop_intel")
    p.add_argument("--indices-table", type=str, default="crop_indices")
    p.add_argument("--date-column", type=str, default=None, help="index_date or analysis_date (default: auto-detect)")
    p.add_argument("--field-locations-schema", type=str, default="operations")
    p.add_argument("--polygon-schema", type=str, default="operations")
    p.add_argument("--sar-vv-col", type=str, default="VV", help="Linear VV column (quoted if mixed case)")
    p.add_argument("--sar-vh-col", type=str, default="VH", help="Linear VH column")
    p.add_argument("--s3-bucket", type=str, default=S3_BUCKET)
    p.add_argument("--s3-prefix", action="append", default=[], help="Repeatable S3 prefixes for KML lookup")
    p.add_argument("--build-s3-index", action="store_true", help="List KML under prefixes for basename lookup")
    p.add_argument("--local-root", type=str, default=None, help="Local tree to find KML by basename")
    p.add_argument("--workers", type=int, default=4, help="Parallel API workers (per-date compute)")
    p.add_argument("--batch-size", type=int, default=50, help="Rows per DB commit")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    location_ids = [x.strip() for x in args.location_ids.split(",") if x.strip()]
    location_ids = list(dict.fromkeys(location_ids))

    db = SessionLocal()
    date_col = args.date_column
    if not date_col:
        date_col = _detect_date_column(db, schema=args.indices_schema, table=args.indices_table)
    logger.info("Using date column: %s", date_col)

    set_vh_vv = _column_exists(db, schema=args.indices_schema, table=args.indices_table, column="vh_vv_ratio")
    set_lst = _column_exists(db, schema=args.indices_schema, table=args.indices_table, column="lst_celsius")
    if not set_lst:
        logger.warning("Column lst_celsius not found; LST will not be updated (SAR only).")

    rows = _fetch_rows(
        db,
        indices_schema=args.indices_schema,
        indices_table=args.indices_table,
        date_col=date_col,
        location_ids=location_ids,
    )
    db.close()

    if not rows:
        logger.error("No rows matched for given location_ids.")
        sys.exit(1)

    logger.info("Rows to process: %d", len(rows))

    s3_prefixes = args.s3_prefix or []
    basename_index: dict[str, str] | None = None
    s3_client = None
    if s3_prefixes or args.build_s3_index:
        s3_client = _s3_client()
        if args.build_s3_index and s3_prefixes:
            logger.info("Building S3 basename index...")
            basename_index = _build_s3_basename_index(
                s3_client, bucket=args.s3_bucket, prefixes=s3_prefixes
            )
            logger.info("Index size: %d", len(basename_index))
    elif args.local_root is None:
        logger.warning("Nor s3-prefix nor local-root: geometry may rely only on DB polygon or fail.")

    tmpdir = Path(tempfile.mkdtemp(prefix="sar_lst_patch_"))
    local_root = Path(args.local_root).expanduser() if args.local_root else None

    # Group rows by location_id for geometry resolution
    by_loc: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_loc.setdefault(str(r["location_id"]), []).append(r)

    geo_by_loc: dict[str, dict[str, Any]] = {}
    db2 = SessionLocal()
    try:
        for lid, rs in by_loc.items():
            file_name = str(rs[0]["file_name"])
            geo_by_loc[lid] = _resolve_geojson_for_location(
                db2,
                location_id=lid,
                file_name=file_name,
                polygon_schema=args.polygon_schema,
                field_locations_schema=args.field_locations_schema,
                s3_client=s3_client,
                s3_bucket=args.s3_bucket,
                s3_prefixes=s3_prefixes,
                basename_index=basename_index,
                local_root=local_root,
                tmp_dir=tmpdir,
            )
    finally:
        db2.close()

    def _compute_one(r: dict[str, Any]) -> dict[str, Any]:
        geo = geo_by_loc[str(r["location_id"])]
        d = str(r["obs_date"])[:10]
        lst_c, vv_db, vh_db, vh_vv = _lst_sar_polygon_means_for_analysis_date(geo, d)
        vv_lin = _db_to_linear(vv_db)
        vh_lin = _db_to_linear(vh_db)
        out = {"id": int(r["id"]), "vv": vv_lin, "vh": vh_lin}
        if set_vh_vv:
            out["vh_vv_ratio"] = float(vh_vv) if vh_vv is not None and math.isfinite(float(vh_vv)) else None
        if set_lst:
            out["lst_celsius"] = float(lst_c) if lst_c is not None and math.isfinite(float(lst_c)) else None
        return out

    computed: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as ex:
        futs = [ex.submit(_compute_one, r) for r in rows]
        for fut in as_completed(futs):
            computed.append(fut.result())

    computed.sort(key=lambda x: x["id"])

    upd_sql = _build_update_sql(
        indices_schema=args.indices_schema,
        indices_table=args.indices_table,
        sar_vv_col=args.sar_vv_col,
        sar_vh_col=args.sar_vh_col,
        set_vh_vv=set_vh_vv,
        set_lst=set_lst,
    )
    logger.info("Update SQL: %s", upd_sql)

    if args.dry_run:
        logger.info("[dry-run] First 3 payloads: %s", computed[:3])
        logger.info("[dry-run] No database writes.")
        return

    db3 = SessionLocal()
    total = 0
    try:
        bs = max(1, int(args.batch_size))
        for i in range(0, len(computed), bs):
            chunk = computed[i : i + bs]
            try:
                for row in chunk:
                    params = {"id": row["id"], "vv": row["vv"], "vh": row["vh"]}
                    if set_vh_vv:
                        params["vh_vv_ratio"] = row.get("vh_vv_ratio")
                    if set_lst:
                        params["lst_celsius"] = row.get("lst_celsius")
                    db3.execute(text(upd_sql), params)
                db3.commit()
                total += len(chunk)
                logger.info("Committed batch rows=%d (total=%d)", len(chunk), total)
            except Exception:
                db3.rollback()
                logger.exception("Batch failed; rolled back batch starting index %s", i)
                raise
    finally:
        db3.close()

    logger.info("Done. Rows updated: %d", total)
    print("--- Validation (run in SQL client) ---")
    lids = ",".join("'{}'".format(x.replace("'", "''")) for x in location_ids)
    print(
        f"""
SELECT location_id,
       COUNT(DISTINCT {_quote_ident(args.sar_vv_col)}) AS d_vv,
       COUNT(DISTINCT {_quote_ident(args.sar_vh_col)}) AS d_vh,
       COUNT(DISTINCT lst_celsius) AS d_lst,
       COUNT(*) AS n
FROM {_quote_ident(args.indices_schema)}.{_quote_ident(args.indices_table)}
WHERE location_id IN ({lids})
GROUP BY location_id
ORDER BY location_id;
"""
    )


if __name__ == "__main__":
    main()
