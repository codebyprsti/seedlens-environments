#!/usr/bin/env python3
"""
Backfill SAR + LST in operations.crop_indices without reprocessing S2/indices.

This script updates ONLY:
  - vv_db
  - vh_db
  - vh_vv_ratio
  - lst_celsius

Performance optimizations:
  - Affected location_id prefetch (constant/wrong SAR/LST signatures)
  - Location-level parallel processing (thread/process configurable)
  - Per-location/date in-run cache to avoid duplicate API calls
  - Batched DB updates (single transaction per location)
  - API retry with backoff for transient failures
"""

from __future__ import annotations

import argparse
import logging
import math
import pickle
import sys
import threading
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _install_sh_rate_limit_log_suppression() -> None:
    """
    Sentinel Hub uses warnings.warn() for throttling; filterwarnings often does not match
    across Python/sentinelhub versions. Hook showwarning so logs stay clean (retries unchanged).
    Must run before any code imports sentinelhub.
    """
    _orig = warnings.showwarning

    def _showwarning(message, category, filename, lineno, file=None, line=None):
        try:
            cat_name = getattr(category, "__name__", "") or ""
            if cat_name == "SHRateLimitWarning":
                return
            if "rate limit hit" in str(message).lower():
                return
            if filename and "sentinelhub" in filename.replace("\\", "/").lower() and "rate limit" in str(
                message
            ).lower():
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
    DEFAULT_SEASON_ID,
    _lst_sar_polygon_means_for_analysis_date,
)

logger = logging.getLogger(__name__)


def _setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Keep high-volume per-request logs quiet unless explicit DEBUG is requested.
    if log_level.upper() != "DEBUG":
        logging.getLogger("crop_monitoring.sentinel_client").setLevel(logging.WARNING)


def _centroid_square_geojson(lat: float, lon: float, half_size_deg: float = 0.00045) -> dict[str, Any]:
    min_lon = float(lon) - half_size_deg
    max_lon = float(lon) + half_size_deg
    min_lat = float(lat) - half_size_deg
    max_lat = float(lat) + half_size_deg
    return {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat],
            [max_lon, min_lat],
            [max_lon, max_lat],
            [min_lon, max_lat],
            [min_lon, min_lat],
        ]],
    }


def _as_not_null(value: Optional[float], current: Optional[float], *, default: float = 0.0) -> float:
    if value is not None and math.isfinite(float(value)):
        return float(value)
    if current is not None and math.isfinite(float(current)):
        return float(current)
    return float(default)


def _resolve_metric_columns(db) -> tuple[str, str, str, str, str]:
    q = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'operations'
          AND table_name = 'crop_indices'
          AND column_name IN (
            'vv_db', 'vh_db', 'vv', 'vh',
            'vh_vv_ratio', 'vh_vv',
            'lst_celsius', 'lst',
            'analysis_date', 'index_date'
          )
        """
    )
    cols = {str(r[0]) for r in db.execute(q).fetchall() if r and r[0]}
    vv_col = "vv_db" if "vv_db" in cols else "vv"
    vh_col = "vh_db" if "vh_db" in cols else "vh"
    ratio_col = "vh_vv_ratio" if "vh_vv_ratio" in cols else "vh_vv"
    lst_col = "lst_celsius" if "lst_celsius" in cols else "lst"
    date_col = "analysis_date" if "analysis_date" in cols else "index_date"
    if vv_col not in cols or vh_col not in cols or ratio_col not in cols or lst_col not in cols or date_col not in cols:
        raise RuntimeError(f"Could not detect metric columns in operations.crop_indices. Found: {sorted(cols)}")
    return vv_col, vh_col, ratio_col, lst_col, date_col


def _apply_bulk_updates(
    db,
    updates: list[dict[str, Any]],
    *,
    vv_col: str,
    vh_col: str,
    ratio_col: str,
    lst_col: str,
) -> int:
    if not updates:
        return 0
    q = text(
        f"""
        UPDATE operations.crop_indices
        SET
            {vv_col} = :vv,
            {vh_col} = :vh,
            {ratio_col} = :vh_vv_ratio,
            {lst_col} = :lst_celsius
        WHERE id = :id
        """
    )
    db.execute(q, updates)
    return len(updates)


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _load_disk_cache(path: Optional[str]) -> dict[tuple[float, float, str, str], float]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with p.open("rb") as f:
            data = pickle.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning("Disk cache load failed (%s): %s", p, e)
    return {}


def _save_disk_cache(path: Optional[str], cache: dict[tuple[float, float, str, str], float]) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with p.open("wb") as f:
            pickle.dump(cache, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        logger.warning("Disk cache save failed (%s): %s", p, e)


def _fetch_affected_location_ids(
    db,
    *,
    season_id: str,
    location_id: Optional[str],
    location_limit: Optional[int],
    vv_col: str,
    vh_col: str,
    lst_col: str,
    date_col: str,
) -> list[str]:
    q = text(
        f"""
        SELECT ci.location_id, MIN(ci.id) AS min_id
        FROM operations.crop_indices ci
        JOIN operations.field_locations fl ON fl.location_id = ci.location_id
        WHERE ci.location_id IS NOT NULL
          AND ci.{date_col} IS NOT NULL
          AND (:season_id IS NULL OR ci.season_id = :season_id)
          AND (:location_id IS NULL OR ci.location_id = :location_id)
        GROUP BY ci.location_id
        HAVING
            COUNT(DISTINCT ci.{vv_col}) <= 1
            OR COUNT(DISTINCT ci.{vh_col}) <= 1
            OR COUNT(DISTINCT ci.{lst_col}) <= 1
        ORDER BY min_id
        """
    )
    rows = db.execute(q, {"season_id": season_id, "location_id": location_id}).fetchall()
    ids = [str(r[0]) for r in rows if r and r[0]]
    if location_limit is not None:
        ids = ids[: max(0, int(location_limit))]
    return ids


def _fetch_rows_for_location(
    db,
    *,
    season_id: str,
    location_id: str,
    vv_col: str,
    vh_col: str,
    ratio_col: str,
    lst_col: str,
    date_col: str,
) -> list[dict[str, Any]]:
    q = text(
        f"""
        SELECT
            ci.id,
            ci.{date_col}::date AS analysis_date,
            ci.location_id,
            fl.latitude,
            fl.longitude,
            ci.{vv_col} AS vv,
            ci.{vh_col} AS vh,
            ci.{ratio_col} AS vh_vv_ratio,
            ci.{lst_col} AS lst_celsius
        FROM operations.crop_indices ci
        JOIN operations.field_locations fl
          ON fl.location_id = ci.location_id
        WHERE ci.location_id = :location_id
          AND ci.{date_col} IS NOT NULL
          AND (:season_id IS NULL OR ci.season_id = :season_id)
        ORDER BY ci.id
        """
    )
    rows = db.execute(q, {"location_id": location_id, "season_id": season_id}).mappings().all()
    return [dict(r) for r in rows]


def _compute_metrics_with_retry(
    *,
    lat: float,
    lon: float,
    analysis_date: str,
    retries: int,
    api_slot: Optional[threading.Semaphore],
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    lat_r = round(float(lat), 7)
    lon_r = round(float(lon), 7)
    date_s = str(analysis_date)[:10]
    geo = _centroid_square_geojson(lat_r, lon_r)

    def _call_api() -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        if api_slot is not None:
            with api_slot:
                return _lst_sar_polygon_means_for_analysis_date(geo, date_s)
        return _lst_sar_polygon_means_for_analysis_date(geo, date_s)

    last_err: Optional[Exception] = None
    attempts = max(1, int(retries) + 1)
    for attempt in range(1, attempts + 1):
        try:
            return _call_api()
        except Exception as e:
            last_err = e
            if attempt >= attempts:
                break
            sleep_s = 1.0 * attempt
            logger.warning(
                "API retry location(lat=%.6f lon=%.6f date=%s) attempt=%d/%d err=%s",
                lat_r,
                lon_r,
                date_s,
                attempt,
                attempts,
                e,
            )
            time.sleep(sleep_s)
    logger.error("API failed after retries for date=%s lat=%.6f lon=%.6f: %s", date_s, lat_r, lon_r, last_err)
    return None, None, None, None


def _process_one_location(
    location_id: str,
    *,
    season_id: str,
    per_date_workers: int,
    update_batch_size: int,
    dry_run: bool,
    retries: int,
    api_slot: Optional[threading.Semaphore],
    vv_col: str,
    vh_col: str,
    ratio_col: str,
    lst_col: str,
    date_col: str,
) -> dict[str, int]:
    db = SessionLocal()
    try:
        rows = _fetch_rows_for_location(
            db,
            season_id=season_id,
            location_id=location_id,
            vv_col=vv_col,
            vh_col=vh_col,
            ratio_col=ratio_col,
            lst_col=lst_col,
            date_col=date_col,
        )
        if not rows:
            return {"locations_processed": 1, "rows_scanned": 0, "rows_updated": 0, "rows_skipped": 0}

        logger.debug("Updating SAR/LST for location: %s", location_id)

        first = rows[0]
        lat = float(first["latitude"])
        lon = float(first["longitude"])
        unique_dates = sorted({str(r["analysis_date"]) for r in rows})
        loc_cache: dict[tuple[str, str], tuple[Optional[float], Optional[float], Optional[float], Optional[float]]] = {}

        def _task(ad: str) -> tuple[str, tuple[Optional[float], Optional[float], Optional[float], Optional[float]]]:
            key = (location_id, ad[:10])
            if key in loc_cache:
                return ad, loc_cache[key]
            vals = _compute_metrics_with_retry(
                lat=lat,
                lon=lon,
                analysis_date=ad[:10],
                retries=retries,
                api_slot=api_slot,
            )
            loc_cache[key] = vals
            return ad, vals

        metrics_map: dict[str, tuple[Optional[float], Optional[float], Optional[float], Optional[float]]] = {}
        with ThreadPoolExecutor(max_workers=max(1, int(per_date_workers))) as ex:
            futures = [ex.submit(_task, ad) for ad in unique_dates]
            for fut in as_completed(futures):
                ad, vals = fut.result()
                metrics_map[ad] = vals

        updates: list[dict[str, Any]] = []
        skipped = 0
        for r in rows:
            date_s = str(r["analysis_date"])
            lst_c, vv_db, vh_db, vh_vv_ratio = metrics_map.get(date_s, (None, None, None, None))
            new_vv = _as_not_null(vv_db, r.get("vv"))
            new_vh = _as_not_null(vh_db, r.get("vh"))
            new_ratio = _as_not_null(vh_vv_ratio, r.get("vh_vv_ratio"))
            new_lst = _as_not_null(lst_c, r.get("lst_celsius"))

            if (
                r.get("vv") is not None
                and r.get("vh") is not None
                and r.get("vh_vv_ratio") is not None
                and r.get("lst_celsius") is not None
                and float(r.get("vv")) == float(new_vv)
                and float(r.get("vh")) == float(new_vh)
                and float(r.get("vh_vv_ratio")) == float(new_ratio)
                and float(r.get("lst_celsius")) == float(new_lst)
            ):
                skipped += 1
                continue

            updates.append(
                {
                    "id": int(r["id"]),
                    "vv": new_vv,
                    "vh": new_vh,
                    "vh_vv_ratio": new_ratio,
                    "lst_celsius": new_lst,
                }
            )

        updates.sort(key=lambda x: x["id"])
        total_updated = 0
        if dry_run:
            logger.info("[dry-run] Prepared updates for location=%s rows=%d", location_id, len(updates))
        else:
            for part in _chunked(updates, max(1, int(update_batch_size))):
                total_updated += _apply_bulk_updates(
                    db,
                    part,
                    vv_col=vv_col,
                    vh_col=vh_col,
                    ratio_col=ratio_col,
                    lst_col=lst_col,
                )
            db.commit()

        return {
            "locations_processed": 1,
            "rows_scanned": len(rows),
            "rows_updated": total_updated if not dry_run else 0,
            "rows_skipped": skipped,
        }
    except Exception:
        db.rollback()
        logger.exception("Error while processing location_id=%s", location_id)
        return {"locations_processed": 0, "rows_scanned": 0, "rows_updated": 0, "rows_skipped": 0}
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill SAR/LST in crop_indices for affected location_ids only."
    )
    parser.add_argument("--season-id", default=DEFAULT_SEASON_ID, help="Season filter (default: RABI_25_26)")
    parser.add_argument("--location-id", type=str, default=None, help="Update only this location_id across all analysis dates")
    parser.add_argument("--workers", type=int, default=8, help="Parallel workers at location level")
    parser.add_argument("--executor", choices=["thread", "process"], default="thread", help="Parallel executor type")
    parser.add_argument("--per-date-workers", type=int, default=4, help="Parallel workers per location (date-level)")
    parser.add_argument(
        "--api-concurrency",
        type=int,
        default=10,
        metavar="N",
        help="Max concurrent Sentinel API computations (caps CDSE rate limits). Use 0 for no global cap.",
    )
    parser.add_argument("--update-batch-size", type=int, default=1000, help="DB bulk update batch size")
    parser.add_argument("--location-limit", type=int, default=None, help="Limit number of affected location_ids")
    parser.add_argument("--retries", type=int, default=2, help="API retries per date on failure")
    parser.add_argument("--progress-every", type=int, default=100, help="Progress log cadence by locations")
    parser.add_argument("--disk-cache-path", type=str, default=None, help="Optional pickle cache file path (thread mode)")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions; do not write DB")
    parser.add_argument("--log-level", type=str, default="INFO", help="DEBUG, INFO, WARNING")
    args = parser.parse_args()

    _setup_logging(args.log_level)
    start_wall = time.perf_counter()

    updated_total = 0
    processed_rows = 0
    processed_locations = 0
    skipped_total = 0
    total_scanned = 0
    api_cache = _load_disk_cache(args.disk_cache_path)
    api_cache_lock = threading.Lock()
    api_slot: Optional[threading.Semaphore] = None
    if int(args.api_concurrency) > 0:
        api_slot = threading.Semaphore(int(args.api_concurrency))

    db_main = SessionLocal()
    try:
        vv_col, vh_col, ratio_col, lst_col, date_col = _resolve_metric_columns(db_main)
        logger.info(
            "Detected columns vv=%s vh=%s ratio=%s lst=%s date=%s",
            vv_col,
            vh_col,
            ratio_col,
            lst_col,
            date_col,
        )
        target_locations = _fetch_affected_location_ids(
            db_main,
            season_id=args.season_id,
            location_id=args.location_id,
            location_limit=args.location_limit,
            vv_col=vv_col,
            vh_col=vh_col,
            lst_col=lst_col,
            date_col=date_col,
        )
        total_scanned = len(target_locations)
        logger.info("Affected location_ids to process: %d", total_scanned)

        ex_cls = ThreadPoolExecutor if args.executor == "thread" else ProcessPoolExecutor
        with ex_cls(max_workers=max(1, int(args.workers))) as ex:
            futures = [
                ex.submit(
                    _process_one_location,
                    location_id,
                    season_id=args.season_id,
                    per_date_workers=args.per_date_workers,
                    update_batch_size=args.update_batch_size,
                    dry_run=args.dry_run,
                    retries=args.retries,
                    api_slot=api_slot,
                    vv_col=vv_col,
                    vh_col=vh_col,
                    ratio_col=ratio_col,
                    lst_col=lst_col,
                    date_col=date_col,
                )
                for location_id in target_locations
            ]
            for idx, fut in enumerate(as_completed(futures), start=1):
                stats = fut.result()
                processed_locations += int(stats.get("locations_processed", 0))
                processed_rows += int(stats.get("rows_scanned", 0))
                updated_total += int(stats.get("rows_updated", 0))
                skipped_total += int(stats.get("rows_skipped", 0))
                if idx % max(1, int(args.progress_every)) == 0:
                    logger.info(
                        "Progress: %d/%d locations done | rows_scanned=%d rows_updated=%d rows_skipped=%d",
                        idx,
                        total_scanned,
                        processed_rows,
                        updated_total,
                        skipped_total,
                    )

    finally:
        db_main.close()
        if args.executor == "thread":
            _save_disk_cache(args.disk_cache_path, api_cache)

    elapsed = time.perf_counter() - start_wall
    print("---------------------------------")
    print("SAR/LST BACKFILL SUMMARY")
    print("---------------------------------")
    print(f"Season: {args.season_id}")
    print(f"Locations scanned: {total_scanned}")
    print(f"Locations processed: {processed_locations}")
    print(f"Rows scanned: {processed_rows}")
    print(f"Rows updated: {updated_total if not args.dry_run else 0}")
    print(f"Rows skipped: {skipped_total}")
    print(f"Dry run: {args.dry_run}")
    print(f"Elapsed: {elapsed:.1f} seconds")
    print("---------------------------------")


if __name__ == "__main__":
    main()
