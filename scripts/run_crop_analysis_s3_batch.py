#!/usr/bin/env python3
"""
Batch crop analysis from KML files (S3 or local) using Statistical API (Rabi season, one request per file).

Modes:
  crop_indices (default) — Statistical API + operations.crop_indices; uses field_locations lookup only (no Google).
  locations_only — Backfill operations.field_locations only: polygon-first KML (see kml_parser), rounded
    centroid, skip if ROUND(lat,7)/ROUND(lon,7) already exists; else Google reverse geocode + insert.
    Does not run Sentinel or write crop_indices.

Resumable (crop_indices): skip file if (file_name, season_id) already in crop_indices.

S3: Secrets Manager "MyLambdaCredentials" (ap-south-1). Local: --local-dir.

Usage:
  python scripts/run_crop_analysis_s3_batch.py
  python scripts/run_crop_analysis_s3_batch.py --mode locations_only
  python scripts/run_crop_analysis_s3_batch.py --prefix "seedworks/kml_files/input_files/CG/CG/" --mode locations_only
  python scripts/run_crop_analysis_s3_batch.py --local-dir /path/to/kml --mode locations_only --limit 100
  python scripts/run_crop_analysis_s3_batch.py --dry-run
"""

from __future__ import annotations

import hashlib
import logging
import os
import numpy as np
import random
import re
import sys
import tempfile
import time
import warnings
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
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


def _install_sh_rate_limit_log_suppression() -> None:
    """Suppress Sentinel Hub rate-limit warnings (client retries). Before sentinelhub import."""
    _orig = warnings.showwarning

    def _showwarning(message, category, filename, lineno, file=None, line=None):
        try:
            if getattr(category, "__name__", "") == "SHRateLimitWarning":
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# S3 and Secrets Manager
AWS_REGION = "ap-south-1"
SECRET_NAME = "MyLambdaCredentials"
S3_BUCKET = "prsti-public-data"
# Default: CG/CG folder (process all KMLs for crop_indices; override with --prefix)
S3_PREFIX = "seedworks/kml_files/input_files/CG/CG/"

# Rabi season: Dec 1 to today (if today < Dec 1, use previous year's Dec 1)
def get_rabi_season_date_range():
    """Start = Dec 1 (current or previous year if today < Dec 1), end = today."""
    today = date.today()
    if today.month >= 12:
        start = date(today.year, 12, 1)
    else:
        start = date(today.year - 1, 12, 1)
    return start, today


DEFAULT_START_DATE, DEFAULT_END_DATE = get_rabi_season_date_range()
DEFAULT_SEASON_ID = "RABI_25_26"


def _polygon_area_ha(geojson: dict) -> float:
    """Polygon area in hectares (WGS84 -> Web Mercator). Reuses pipeline logic."""
    try:
        from shapely.geometry import shape
        from shapely.ops import transform
        import pyproj
        geom = shape(geojson)
        proj = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        geom_m = transform(proj.transform, geom)
        return float(geom_m.area / 10_000.0)
    except Exception:
        return float("nan")


def get_aws_credentials():
    """Load aws_access_key_id and aws_secret_access_key from Secrets Manager."""
    try:
        import boto3
        import json
        client = boto3.client("secretsmanager", region_name=AWS_REGION)
        resp = client.get_secret_value(SecretId=SECRET_NAME)
        secret = resp.get("SecretString")
        if not secret:
            raise ValueError("SecretString is empty")
        data = json.loads(secret) if isinstance(secret, str) else secret
        access_key = data.get("aws_access_key_id") or data.get("AccessKeyId")
        secret_key = data.get("aws_secret_access_key") or data.get("SecretAccessKey")
        if not access_key or not secret_key:
            raise ValueError("aws_access_key_id and aws_secret_access_key not found in secret")
        return {"aws_access_key_id": access_key, "aws_secret_access_key": secret_key}
    except Exception as e:
        logger.exception("Failed to get credentials from Secrets Manager")
        raise RuntimeError(f"Cannot get AWS credentials from {SECRET_NAME}: {e}") from e


def list_kml_from_local(local_dir: str | Path) -> list[Path]:
    """List all .kml files under local_dir (recursive). Returns list of Paths. Skips empty files."""
    root = Path(local_dir).resolve()
    if not root.is_dir():
        return []
    paths = sorted(root.rglob("*.kml"), key=lambda p: str(p).lower())
    out = []
    for p in paths:
        if not p.is_file():
            continue
        try:
            if p.stat().st_size == 0:
                continue
        except OSError:
            continue
        out.append(p)
    return out


def list_kml_keys(s3_client, prefix: str | None = None):
    """List S3 object keys under prefix that end with .kml; skips empty files. Uses s3_file_loader when available."""
    use_prefix = (prefix or S3_PREFIX).rstrip("/").strip()
    try:
        from crop_monitoring.s3_file_loader import list_kml_files
        return list_kml_files(s3_client, S3_BUCKET, use_prefix, skip_empty=True)
    except ImportError:
        use_prefix = use_prefix + "/" if use_prefix else ""
        paginator = s3_client.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=use_prefix):
            for obj in page.get("Contents") or []:
                k = obj.get("Key") or ""
                if k.endswith(".kml") and (obj.get("Size") or 0) > 0:
                    keys.append(k)
        return sorted(keys)


def _normalize_kml_basename(name: str) -> str:
    s = name.replace("+", " ").strip()
    return re.sub(r"\s+", " ", s).lower()


def _s3_index_cache_path(prefix: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", (prefix or S3_PREFIX).strip("/"))[:120]
    return _root / "tmp" / f"s3_kml_index_{safe}.json"


def build_s3_basename_index(s3_client, prefix: str | None = None) -> dict[str, str]:
    """Map KML basename -> full S3 key under prefix."""
    use_prefix = (prefix or S3_PREFIX).rstrip("/").strip()
    index: dict[str, str] = {}
    for key in list_kml_keys(s3_client, use_prefix):
        base = Path(key).name
        index.setdefault(base, key)
    return index


def load_or_build_s3_basename_index(s3_client, prefix: str | None = None) -> dict[str, str]:
    """Load cached basename index or build and persist it."""
    import json

    cache = _s3_index_cache_path(prefix or S3_PREFIX)
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                logger.info("Loaded S3 basename index from cache: %s (%d entries)", cache, len(data))
                return {str(k): str(v) for k, v in data.items()}
        except Exception as e:
            logger.warning("Could not read S3 index cache %s: %s", cache, e)

    index = build_s3_basename_index(s3_client, prefix)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(index, indent=0), encoding="utf-8")
        logger.info("Wrote S3 basename index cache: %s (%d entries)", cache, len(index))
    except Exception as e:
        logger.warning("Could not write S3 index cache: %s", e)
    return index


def resolve_s3_keys_for_basenames(
    s3_client,
    basenames: list[str],
    *,
    prefix: str | None = None,
    basename_index: dict[str, str] | None = None,
) -> list[str]:
    """Resolve S3 object keys for KML basenames without listing the full bucket each run."""
    index = basename_index or load_or_build_s3_basename_index(s3_client, prefix)
    norm_to_base = {_normalize_kml_basename(b): b for b in index}
    keys: list[str] = []
    for raw in basenames:
        norm = _normalize_kml_basename(raw)
        base = norm_to_base.get(norm)
        if base and base in index:
            keys.append(index[base])
    return keys


def download_kml(s3_client, key: str, local_path: Path) -> None:
    try:
        from crop_monitoring.s3_file_loader import download_kml as s3_download
        s3_download(s3_client, S3_BUCKET, key, local_path)
    except ImportError:
        s3_client.download_file(S3_BUCKET, key, str(local_path))


def _polygon_id_stable(stable_key: str) -> str:
    """POLY_<16 hex> from SHA-256 prefix of a stable string (S3 key or resolved local path)."""
    h = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:16]
    return f"POLY_{h.upper()}"


S2_BAND_IDS = ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"]

STATISTICAL_BANDS_EVALSCRIPT = """
//VERSION=3
function setup() {
  return {
    input: ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12", "dataMask"],
    output: [
      { id: "bands", bands: 10, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1, sampleType: "UINT8" }
    ]
  };
}
function evaluatePixel(sample) {
  return {
    bands: [
      sample.B02, sample.B03, sample.B04, sample.B05,
      sample.B06, sample.B07, sample.B08, sample.B8A,
      sample.B11, sample.B12
    ],
    dataMask: [sample.dataMask]
  };
}
"""


def fetch_s2_band_means_timeseries(
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    *,
    maxcc: float = 20,
    resolution: int = 10,
    return_raw: bool = False,
) -> list[dict] | tuple[list[dict], Optional[dict]]:
    """Daily (P1D) S2 band means from Statistical API for crop_indices band columns."""
    from sentinelhub import CRS, DataCollection, Geometry, SentinelHubStatistical
    from crop_monitoring.statistical_client import _get_config

    config = _get_config()
    geom = Geometry(geometry_geojson, crs=CRS.WGS84)
    maxcc_01 = float(maxcc) if 0 <= maxcc <= 1 else float(maxcc) / 100.0
    try:
        dc = DataCollection.SENTINEL2_L2A.define_from("s2l2a", service_url=config.sh_base_url)
    except Exception:
        dc = getattr(DataCollection, "SENTINEL2_L2A", DataCollection.SENTINEL2_L2A)
    aggregation = SentinelHubStatistical.aggregation(
        evalscript=STATISTICAL_BANDS_EVALSCRIPT,
        time_interval=(start_date, end_date),
        aggregation_interval="P1D",
        resolution=(resolution, resolution),
    )
    request = SentinelHubStatistical(
        aggregation=aggregation,
        input_data=[SentinelHubStatistical.input_data(dc, maxcc=maxcc_01)],
        geometry=geom,
        config=config,
    )
    stats_list = request.get_data()
    if not stats_list:
        return ([], None) if return_raw else []
    raw_first = stats_list[0] if return_raw else None
    rows: list[dict] = []
    data = stats_list[0].get("data") or []
    for item in data:
        interval = item.get("interval") or {}
        from_ts = interval.get("from", "")
        to_ts = interval.get("to", "")
        analysis_date = to_ts[:10] if len(to_ts) >= 10 else (from_ts[:10] if len(from_ts) >= 10 else None)
        outputs = item.get("outputs") or {}
        ob = outputs.get("bands") or {}
        bands_out = ob.get("bands") if isinstance(ob.get("bands"), dict) else ob
        row = {"analysis_date": analysis_date}
        for i, bid in enumerate(S2_BAND_IDS):
            band_stats = bands_out.get(f"B{i}") or bands_out.get(str(i))
            key = f"SENT2_{bid}"
            if band_stats and isinstance(band_stats.get("stats"), dict):
                mean_v = band_stats["stats"].get("mean")
                row[key] = float(mean_v) if mean_v is not None else None
            else:
                row[key] = None
        rows.append(row)
    if return_raw:
        return rows, raw_first
    return rows


def _summarize_rasters_for_raw(bands_dict: dict[str, np.ndarray] | None) -> dict[str, Any]:
    """JSON-friendly stats per band (no full raster dump)."""
    if not bands_dict:
        return {}
    out: dict[str, Any] = {}
    for k, arr in bands_dict.items():
        a = np.asarray(arr, dtype=np.float64)
        flat = a.ravel()
        fin = np.isfinite(flat)
        out[k] = {
            "shape": list(a.shape),
            "mean": float(np.nanmean(flat)) if flat.size else None,
            "min": float(np.nanmin(flat[fin])) if np.any(fin) else None,
            "max": float(np.nanmax(flat[fin])) if np.any(fin) else None,
        }
    return out


def _store_satellite_raw_row(
    db,
    *,
    location_id: str,
    file_name: str,
    source: str,
    observation_date: Optional[date] = None,
    raw_response: Any = None,
    bands: Any = None,
    indices: Any = None,
) -> None:
    from crop_monitoring.database.raw_observation_repository import insert_satellite_raw_observation

    rid = insert_satellite_raw_observation(
        db,
        location_id=location_id,
        file_name=file_name,
        source=source,
        observation_date=observation_date,
        raw_response=raw_response,
        bands=bands,
        indices=indices,
    )
    if rid is not None:
        logger.info("Raw observation stored")


def _ndwi_gao_from_nir_swir1(nir: float | None, swir1: float | None) -> float | None:
    """NDWI (Gao): (NIR - SWIR1) / (NIR + SWIR1), guarded against zero/invalid denominator."""
    if nir is None or swir1 is None:
        return None
    try:
        n = float(nir)
        s = float(swir1)
    except (TypeError, ValueError):
        return None
    den = n + s
    if not np.isfinite(n) or not np.isfinite(s) or abs(den) < 1e-10:
        return None
    return (n - s) / den


def _lst_sar_polygon_means_for_analysis_date(
    geojson: dict,
    analysis_date_str: str,
    *,
    s1_lookback_days: int = 12,
    lst_pad_before: int = 1,
    lst_pad_after: int = 2,
) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    Polygon-mean LST and SAR for one S2 observation day (aligned with crop_indices row dates).

    S3 SLSTR: short padded window around the day (single-day requests often miss a pass).
    S1 GRD: lookback window ending the day after ``analysis_date`` so the Process API mosaic
    can change as the window slides — unlike one season-wide mean reused for every row.
    """
    from crop_monitoring.sentinel_client import fetch_s3_thermal, fetch_s1_sar
    from crop_monitoring.temperature_calculator import lst_celsius as lst_celsius_array
    from crop_monitoring.sar_calculator import compute_sar_metrics

    try:
        d0 = date.fromisoformat(str(analysis_date_str)[:10])
    except ValueError:
        return None, None, None, None

    s3_from = (d0 - timedelta(days=lst_pad_before)).isoformat()
    s3_to = (d0 + timedelta(days=lst_pad_after + 1)).isoformat()
    s1_from = (d0 - timedelta(days=s1_lookback_days)).isoformat()
    s1_to = (d0 + timedelta(days=1)).isoformat()

    lst_c: Optional[float] = None
    vv_db: Optional[float] = None
    vh_db: Optional[float] = None
    vh_vv_ratio: Optional[float] = None

    try:
        s3 = fetch_s3_thermal(geojson, (s3_from, s3_to))
        if s3 and "S8" in s3 and "S9" in s3:
            lst_arr = lst_celsius_array(s3["S8"], s3["S9"])
            flat = np.asarray(lst_arr).ravel()
            valid = np.isfinite(flat)
            if np.any(valid):
                lst_c = float(np.nanmean(flat[valid]))
    except Exception as e:
        logger.debug("LST per-day fetch failed date=%s: %s", analysis_date_str, e)

    try:
        s1 = fetch_s1_sar(geojson, (s1_from, s1_to))
        if s1 and "VV" in s1 and "VH" in s1:
            vv = np.asarray(s1["VV"], dtype=np.float64)
            vh = np.asarray(s1["VH"], dtype=np.float64)
            valid = np.isfinite(vv) & np.isfinite(vh) & (vv > 0)
            if np.any(valid):
                sar = compute_sar_metrics(vv, vh)
                vv_db = float(np.nanmean(np.asarray(sar["VV_dB"], dtype=np.float64)[valid]))
                vh_db = float(np.nanmean(np.asarray(sar["VH_dB"], dtype=np.float64)[valid]))
                vh_vv_ratio = float(
                    np.nanmean(np.asarray(sar["vh_vv_ratio"], dtype=np.float64)[valid])
                )
    except Exception as e:
        logger.debug("SAR per-day fetch failed date=%s: %s", analysis_date_str, e)

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "per-day LST/SAR date=%s s3=[%s,%s) s1=[%s,%s) lst=%s vv_db=%s vh_db=%s vh_vv=%s",
            analysis_date_str,
            s3_from,
            s3_to,
            s1_from,
            s1_to,
            lst_c,
            vv_db,
            vh_db,
            vh_vv_ratio,
        )
    return lst_c, vv_db, vh_db, vh_vv_ratio


# --- field_locations backfill (locations_only); helpers local to this script (no new package) ---

_NORMALIZE_VILLAGE_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _extract_village_from_placemark_name(name: str) -> str | None:
    """First token of placemark name, lowercased, punctuation stripped (e.g. AMLIPARA ANIL... -> amlipara)."""
    if not name or not str(name).strip():
        return None
    text = str(name).strip().replace("_", " ")
    tokens = text.split()
    if not tokens:
        return None
    first = tokens[0].strip()
    for prefix in ("village-", "village_", "village:"):
        if first.lower().startswith(prefix):
            first = first[len(prefix) :].strip()
            break
    normalized = _NORMALIZE_VILLAGE_PUNCT.sub("", first).strip().lower()
    return normalized if normalized else None


def _field_location_exists_rounded_coords(db, lat: float, lon: float) -> bool:
    """True if a row exists at the same centroid when lat/lon match at 7 decimal places (PostgreSQL ROUND)."""
    from sqlalchemy import text

    row = db.execute(
        text("""
            SELECT 1 FROM operations.field_locations
            WHERE ROUND(CAST(latitude AS NUMERIC), 7) = ROUND(CAST(:lat AS NUMERIC), 7)
              AND ROUND(CAST(longitude AS NUMERIC), 7) = ROUND(CAST(:lon AS NUMERIC), 7)
            LIMIT 1
        """),
        {"lat": lat, "lon": lon},
    ).fetchone()
    return row is not None


def _google_reverse_geocode_with_retry(
    lat: float,
    lon: float,
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> dict:
    """Call Google reverse geocode; retry a few times if the result is empty (transient / parsing)."""
    from services.google_reverse_geocode import get_location_from_coordinates

    last: dict = {}
    for attempt in range(max_attempts):
        last = get_location_from_coordinates(lat, lon)
        if last.get("village") or last.get("district") or last.get("state"):
            return last
        if attempt < max_attempts - 1:
            wait = base_delay * (2**attempt) + random.uniform(0.1, 0.4)
            logger.warning(
                "Google geocode returned empty admin fields; retry %s/%s in %.1fs",
                attempt + 1,
                max_attempts,
                wait,
            )
            time.sleep(wait)
    return last


def run_locations_only_backfill(
    db,
    keys: list,
    *,
    use_local: bool,
    s3_client,
    tmp: Path | None,
    geocode_delay_sec: float = 0.35,
) -> tuple[int, int, int, list[str]]:
    """
    For each KML: parse (polygon-first in kml_parser), centroid 7dp, skip if ROUND match exists;
    else Google reverse geocode + get_field_location_id. Sequential to limit API load.
    """
    from crop_monitoring.kml_parser import parse_kml
    from crop_monitoring.database.location_repository import get_field_location_id
    from shapely.geometry import shape

    inserted = 0
    skipped_existing = 0
    errors = 0
    failed: list[str] = []

    for i, key in enumerate(keys, start=1):
        file_name = key.name if hasattr(key, "name") else Path(key).name
        print("---------------------------------")
        print(f"[locations_only] {i}/{len(keys)}: {file_name}")

        if use_local:
            local_path = key
            if not local_path.is_file():
                logger.warning("Missing file: %s", local_path)
                errors += 1
                failed.append(file_name)
                continue
        else:
            assert tmp is not None and s3_client is not None
            local_path = tmp / file_name
            try:
                download_kml(s3_client, key, local_path)
            except Exception as e:
                logger.exception("Download failed: %s", key)
                print(f"  Download failed: {e}")
                errors += 1
                failed.append(file_name)
                continue

        try:
            geojson, metadata_kml = parse_kml(local_path)
            placemark_name = metadata_kml.get("placemark_name") or ""
            geom = shape(geojson)
            centroid = geom.centroid
            lon_c = round(float(centroid.x), 7)
            lat_c = round(float(centroid.y), 7)
            logger.info("Centroid: %s, %s", lat_c, lon_c)
        except Exception as e:
            logger.exception("KML parse/centroid failed: %s", file_name)
            print(f"  Parse failed: {e}")
            errors += 1
            failed.append(file_name)
            continue

        try:
            if _field_location_exists_rounded_coords(db, lat_c, lon_c):
                logger.info("Location found → skipping Google API")
                print("  Location already exists -> skipping")
                skipped_existing += 1
                continue

            logger.info("Location not found → calling Google API")
            geo = _google_reverse_geocode_with_retry(lat_c, lon_c)
            time.sleep(geocode_delay_sec + random.uniform(0.0, 0.15))

            village = (geo.get("village") or "").strip() or "Unknown field"
            extracted_village = _extract_village_from_placemark_name(placemark_name)
            loc_id = get_field_location_id(
                db,
                village,
                geo.get("district"),
                geo.get("state"),
                geo.get("state_code"),
                geo.get("mandal"),
                geo.get("postcode"),
                lat_c,
                lon_c,
                extracted_village=extracted_village,
            )
            if loc_id:
                logger.info("Inserted new location: %s", loc_id)
                print(f"  Inserted new location: {loc_id}")
                inserted += 1
            else:
                logger.warning("Insert returned no location_id for %s", file_name)
                errors += 1
                failed.append(file_name)
        except Exception as e:
            logger.exception("field_locations backfill failed: %s", file_name)
            print(f"  Failed: {e}")
            try:
                db.rollback()
            except Exception:
                pass
            errors += 1
            failed.append(file_name)

    return inserted, skipped_existing, errors, failed


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Batch from KMLs: crop_indices (Statistical API) or locations_only (field_locations backfill)."
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="crop_indices",
        choices=("crop_indices", "locations_only"),
        help="crop_indices: Sentinel + crop_indices (default). locations_only: backfill operations.field_locations only.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max number of KML files to process")
    parser.add_argument(
        "--season-id",
        type=str,
        default=None,
        metavar="ID",
        help=(
            "Season written to crop_indices (default: RABI_25_26). "
            "Use a unique ID (e.g. TEST_3KML) to ingest test rows without skipping existing RABI data."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Only list S3 keys, do not run pipeline")
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Start date for 3-month window (default: 90 days before end)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="End date (default: today)",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default=None,
        metavar="PREFIX",
        help="S3 prefix (ignored if --local-dir set). Default: seedworks/kml_files/input_files/CG/CG/",
    )
    parser.add_argument(
        "--local-dir",
        type=str,
        default=None,
        metavar="PATH",
        help="Read KML files from local directory (recursive). No S3; faster for lab/remote runs.",
    )
    parser.add_argument(
        "--geocode-delay",
        type=float,
        default=0.35,
        metavar="SEC",
        help="Extra delay after each Google geocode call in locations_only mode (default: 0.35; jitter added).",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        metavar="PATH",
        help="Append structured logs to this file (UTF-8) in addition to console.",
    )
    parser.add_argument(
        "--only-file",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "Process only KML basenames that match (repeatable). "
            "Whitespace is collapsed for comparison; use the real .kml name on disk when in doubt."
        ),
    )
    parser.add_argument(
        "--extend-dates",
        action="store_true",
        help=(
            "Backfill a new date window for files already in crop_indices: skip file-level "
            "'already ingested' gate; still skip duplicate observation days."
        ),
    )
    args = parser.parse_args()

    if args.log_file:
        log_path = Path(args.log_file).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8", mode="a")
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logging.getLogger().addHandler(fh)
        logger.info("Logging to file: %s", log_path)

    use_local = bool(args.local_dir)
    keys = []
    total_files = 0
    s3_client = None

    if use_local:
        local_root = Path(args.local_dir).resolve()
        if not local_root.is_dir():
            print(f"Error: --local-dir is not a directory: {local_root}", file=sys.stderr)
            sys.exit(1)
        keys = list_kml_from_local(local_root)
        total_files = len(keys)
        print(f"Local dir: {local_root}")
        print(f"Found {total_files} KML file(s)")
    else:
        try:
            import boto3
        except ImportError:
            print("Error: boto3 required for S3 mode. pip install boto3", file=sys.stderr)
            sys.exit(1)
        creds = get_aws_credentials()
        s3_client = boto3.client(
            "s3",
            region_name=AWS_REGION,
            aws_access_key_id=creds["aws_access_key_id"],
            aws_secret_access_key=creds["aws_secret_access_key"],
        )
        s3_prefix = args.prefix or S3_PREFIX
        if args.only_file:
            allow_basenames = [
                re.sub(r"\s+", " ", n.replace("+", " ").strip())
                for n in args.only_file
                if n and n.strip()
            ]
            basename_index = load_or_build_s3_basename_index(s3_client, s3_prefix)
            keys = resolve_s3_keys_for_basenames(
                s3_client,
                allow_basenames,
                prefix=s3_prefix,
                basename_index=basename_index,
            )
            total_files = len(basename_index)
            print(f"S3 s3://{S3_BUCKET}/{s3_prefix}")
            print(f"Resolved {len(keys)} / {len(allow_basenames)} requested KML(s) via basename index")
        else:
            keys = list_kml_keys(s3_client, prefix=args.prefix)
            total_files = len(keys)
            print(f"S3 s3://{S3_BUCKET}/{s3_prefix}")
            print(f"Found {total_files} KML file(s)")

    if args.limit is not None:
        keys = keys[: args.limit]
        print(f"Processing limit: {len(keys)} files")

    if args.only_file and use_local:
        def _kml_basename_norm(p) -> str:
            name = p.name if hasattr(p, "name") else Path(str(p)).name
            return _normalize_kml_basename(name)

        allow = {_normalize_kml_basename(n) for n in args.only_file if n and n.strip()}
        before = len(keys)
        keys = [k for k in keys if _kml_basename_norm(k) in allow]
        print(f"--only-file filter: {before} -> {len(keys)} file(s)")
    elif args.only_file and not use_local:
        print(f"--only-file: using {len(keys)} resolved S3 key(s)")

    path_disp = str(Path(args.local_dir).resolve()) if use_local else (args.prefix or S3_PREFIX)
    logger.info("Run start: mode=%s path=%s kml_count=%d", args.mode, path_disp, len(keys))
    if not keys:
        print("No KML files to process.")
        sys.exit(0)

    if args.dry_run:
        for i, k in enumerate(keys[:20], 1):
            disp = k.name if hasattr(k, "name") else k
            print(f"  {i}. {disp}")
        if total_files > 20:
            print(f"  ... and {total_files - 20} more")
        sys.exit(0)

    # --- locations_only: S3/local KML -> polygon centroid -> skip if exists -> Google -> insert ---
    if args.mode == "locations_only":
        db = None
        try:
            from core.db import SessionLocal
            from sqlalchemy import text

            db = SessionLocal()
            db.execute(text("SELECT 1"))
        except Exception as e:
            print(f"Error: DB not available: {e}", file=sys.stderr)
            sys.exit(1)

        start_wall = time.perf_counter()
        tmpdir_ctx = tempfile.TemporaryDirectory(prefix="crop_loc_kml_") if not use_local else nullcontext(Path("."))
        with tmpdir_ctx as tmpdir:
            tmp = Path(tmpdir) if not use_local else None
            inserted, skipped_existing, err_count, failed = run_locations_only_backfill(
                db,
                keys,
                use_local=use_local,
                s3_client=s3_client,
                tmp=tmp,
                geocode_delay_sec=max(0.0, float(args.geocode_delay)),
            )
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
        elapsed = time.perf_counter() - start_wall
        print("---------------------------------")
        print("LOCATIONS_ONLY SUMMARY")
        print("---------------------------------")
        print(f"Total KML files listed: {total_files}")
        print(f"Processed this run: {len(keys)}")
        print(f"Inserted new locations: {inserted}")
        print(f"Skipped (already exists at centroid): {skipped_existing}")
        print(f"Errors: {err_count}")
        print(f"Wall time: {elapsed:.1f} s")
        if failed:
            print("Failed files:")
            for k in failed[:40]:
                print(f"  - {k}")
            if len(failed) > 40:
                print(f"  ... and {len(failed) - 40} more")
        print("---------------------------------")
        sys.exit(0 if err_count == 0 else 1)

    end_date = date.fromisoformat(args.end) if args.end else DEFAULT_END_DATE
    start_date = date.fromisoformat(args.start) if args.start else DEFAULT_START_DATE
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()
    run_season_id = (args.season_id or "").strip() or DEFAULT_SEASON_ID
    logger.info("Fetching indices from: %s -> %s (season: %s)", start_str, end_str, run_season_id)

    db = None
    try:
        from core.db import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        db.execute(text("SELECT 1"))
    except Exception as e:
        print(f"Error: DB not available: {e}. Results cannot be saved.", file=sys.stderr)
        sys.exit(1)

    from crop_monitoring.kml_parser import parse_kml
    from crop_monitoring.statistical_client import fetch_s2_indices_timeseries_with_raw
    from crop_monitoring.sentinel_client import fetch_s3_thermal, fetch_s1_sar
    from crop_monitoring.temperature_calculator import lst_celsius as lst_celsius_array
    from crop_monitoring.sar_calculator import compute_sar_metrics
    from crop_monitoring.metadata_resolver import resolve_metadata
    from crop_monitoring.field_location_resolver import resolve_or_create_field_location
    from crop_monitoring.database.repository import (
        get_grower_id,
        get_variety_id,
        insert_crop_indices,
        crop_indices_row_exists,
        crop_indices_observation_exists,
        crop_indices_file_processed_for_season,
        kml_file_name_for_storage,
        load_processed_file_keys_for_season,
    )
    from crop_monitoring.database.location_repository import get_field_location_row_by_centroid
    from crop_monitoring.insert_validator import (
        duplicate_file_ingested,
        new_counters,
        print_validation_batch_summary,
        resolve_location_id_from_centroid,
    )

    total_inserted = 0
    total_skipped_duplicates = 0
    total_skipped_already_processed = 0
    total_errors = 0
    failed_keys: list[str] = []
    start_wall = time.perf_counter()
    processed_keys = load_processed_file_keys_for_season(db, run_season_id)
    logger.info(
        "Resumability: %d distinct file_name keys loaded for season %s",
        len(processed_keys),
        run_season_id,
    )

    tmpdir_ctx = tempfile.TemporaryDirectory(prefix="crop_s3_kml_") if not use_local else nullcontext(Path("."))
    with tmpdir_ctx as tmpdir:
        tmp = Path(tmpdir) if not use_local else None
        for i, key in enumerate(keys, start=1):
            file_name = (key.name if hasattr(key, "name") else Path(key).name).replace("+", " ").strip()
            store_file_name = kml_file_name_for_storage(file_name)
            print("---------------------------------")
            print(f"File {i}/{len(keys)}: {file_name}")
            if store_file_name != file_name:
                print(f"  DB file_name (no parentheses): {store_file_name}")

            # Clear any aborted transaction left by a prior file or swallowed DBAPIError in helpers.
            try:
                db.rollback()
            except Exception:
                pass

            if use_local:
                local_path = key
                if not local_path.is_file():
                    logger.warning("Missing or not a file: %s", local_path)
                    total_errors += 1
                    failed_keys.append(file_name)
                    continue
            else:
                try:
                    local_path = tmp / file_name
                    download_kml(s3_client, key, local_path)
                except Exception as e:
                    logger.exception("Download failed for %s", key)
                    print(f"  Download failed: {e}")
                    total_errors += 1
                    failed_keys.append(file_name)
                    continue

            # Stable across runs: S3 uses bucket+object key (temp file path changes each run).
            if use_local:
                polygon_id = _polygon_id_stable(str(local_path.resolve()))
            else:
                polygon_id = _polygon_id_stable(f"{S3_BUCKET}:{key}")

            try:
                geojson, metadata_kml = parse_kml(local_path)
                placemark_name = metadata_kml.get("placemark_name") or ""
                area_acre = metadata_kml.get("area_acre")
                distance_km = metadata_kml.get("distance_km")
                extracted_village = metadata_kml.get("village_name")
                extracted_grower = metadata_kml.get("extracted_grower")
                logger.info("Processing file: %s", file_name)
                logger.info("Extracted village: %s", extracted_village or "(none)")
                logger.info("Extracted grower: %s", extracted_grower or "(none)")
                logger.info("Area acre: %s", area_acre if area_acre is not None else "(none)")
                logger.info("Distance km: %s", distance_km if distance_km is not None else "(none)")
            except Exception as e:
                logger.exception("KML parse failed for %s", file_name)
                print(f"  KML parse failed: {e}")
                total_errors += 1
                failed_keys.append(file_name)
                continue

            lat_c, lon_c = None, None
            detected_location: dict = {}
            try:
                from shapely.geometry import shape
                geom = shape(geojson)
                centroid = geom.centroid
                lon_c = round(float(centroid.x), 7)
                lat_c = round(float(centroid.y), 7)
                logger.info("Skipping Google API call (using field_locations only)")
            except Exception as e:
                logger.warning("Polygon/centroid failed for %s: %s", file_name, e)

            try:
                resolved = resolve_metadata(
                    kml_name=placemark_name,
                    filename=file_name,
                    detected_location=detected_location,
                    db_session=None,  # grower/variety resolved after location_id below
                    use_llm=False,
                )
                village = resolved.get("village")
                grower = resolved.get("grower_name")
                variety = resolved.get("variety")

                # Lookup location_id and village from field_locations by centroid; skip file if no match
                if lat_c is None or lon_c is None:
                    logger.warning("Missing coordinates for %s; skipping", file_name)
                    total_errors += 1
                    failed_keys.append(file_name)
                    continue
                fl_row = get_field_location_row_by_centroid(db, lat_c, lon_c)
                if fl_row is None:
                    logger.warning(
                        "No matching location found for centroid (%s, %s); skipping %s",
                        lat_c, lon_c, file_name,
                    )
                    total_errors += 1
                    failed_keys.append(file_name)
                    continue
                loc_id = fl_row["location_id"]
                loc_village = fl_row.get("village") or ""
                resolved_sql_loc = resolve_location_id_from_centroid(db, float(lat_c), float(lon_c))
                if resolved_sql_loc is None:
                    logger.warning(
                        "SKIPPED: No matching location_id found in field_locations for "
                        "lat=%s, lng=%s, file=%s",
                        lat_c,
                        lon_c,
                        file_name,
                    )
                    total_errors += 1
                    failed_keys.append(file_name)
                    continue
                if str(resolved_sql_loc) != str(loc_id):
                    logger.info(
                        "RESOLVED: location_id=%s from centroid lat=%s, lng=%s (was %s)",
                        resolved_sql_loc,
                        lat_c,
                        lon_c,
                        loc_id,
                    )
                loc_id = resolved_sql_loc
                logger.info("Processing KML: %s", file_name)
                logger.info("Centroid: %s, %s", lat_c, lon_c)
                logger.info(
                    "Centroid match found in operations.field_locations (lat=%s, lon=%s)",
                    lat_c,
                    lon_c,
                )
                logger.info(
                    "Location resolved from field_locations: location_id=%s, village=%s",
                    loc_id,
                    loc_village or "(empty)",
                )
                logger.info("Season: %s", run_season_id)

                r_grower = get_grower_id(db, grower, loc_id) if grower else None
                grower_id = r_grower[0] if r_grower else None
                r_var = get_variety_id(db, variety) if variety else None
                var_id = r_var[0] if r_var else None
                crop_id = r_var[3] if r_var and len(r_var) > 3 else None
                crop_name = r_var[4] if r_var and len(r_var) > 4 else None

                polygon_area = _polygon_area_ha(geojson)

                if (
                    not args.extend_dates
                    and duplicate_file_ingested(
                        db, season_id=run_season_id, location_id=str(loc_id), file_name=store_file_name
                    )
                ):
                    logger.warning(
                        "[duplicate_file] SKIPPED: File already ingested — file=%s, location=%s, season=%s",
                        store_file_name,
                        loc_id,
                        run_season_id,
                    )
                    print("  Skipping file (already ingested for this location+season)")
                    print_validation_batch_summary(file_name, {**new_counters(), "duplicate_file": 1})
                    total_skipped_already_processed += 1
                    continue
                elif args.extend_dates and duplicate_file_ingested(
                    db, season_id=run_season_id, location_id=str(loc_id), file_name=store_file_name
                ):
                    logger.info(
                        "[extend_dates] File has prior rows — fetching new observations only: %s",
                        store_file_name,
                    )
                    print("  Extend mode: backfilling new observation dates only")

                # Release DB connection before long Copernicus calls (idle VPN/DB timeouts).
                try:
                    db.rollback()
                except Exception:
                    pass

                # S2 Statistical API only (daily indices + bands). LST/S3 and SAR/S1 are fetched
                # per observation date for crop_indices rows (see day_metrics); season-wide S3/S1
                # below is only for satellite_raw_observation audit blobs.
                s3_start = (end_date - timedelta(days=10)).isoformat()
                rows = None
                rows_bands = None
                raw_s2_indices_payload = None
                raw_s2_bands_payload = None
                s3_bands = None
                s1_bands = None
                try:
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        fut_s2 = executor.submit(
                            fetch_s2_indices_timeseries_with_raw, geojson, start_str, end_str, maxcc=20
                        )
                        fut_s2_bands = executor.submit(
                            fetch_s2_band_means_timeseries,
                            geojson,
                            start_str,
                            end_str,
                            maxcc=20,
                            return_raw=True,
                        )
                        future_to_key = {fut_s2: "s2", fut_s2_bands: "s2_bands"}
                        for fut in as_completed([fut_s2, fut_s2_bands]):
                            key_f = future_to_key[fut]
                            result = fut.result()
                            if key_f == "s2":
                                rows, raw_s2_indices_payload = result
                            else:
                                rows_bands, raw_s2_bands_payload = result
                except Exception as e:
                    logger.exception("Sentinel API failure for %s: %s", file_name, e)
                    print(f"  Sentinel API failed: {e}")
                    total_errors += 1
                    failed_keys.append(file_name)
                    continue

                if not rows:
                    logger.warning("No observation intervals returned for %s; skipping insert", file_name)
                    print("  No index data returned; skipping")
                    continue
                band_by_date = {r.get("analysis_date"): r for r in (rows_bands or []) if r.get("analysis_date")}

                logger.info("Indices fetched successfully for %s (%d intervals)", file_name, len(rows))

                # One season-wide S3 + S1 request for raw table only (full-interval mosaic summary).
                try:
                    s3_bands = fetch_s3_thermal(geojson, (s3_start, end_str))
                except Exception as e:
                    logger.warning("Season LST fetch for raw storage failed for %s: %s", file_name, e)
                try:
                    s1_bands = fetch_s1_sar(geojson, (start_str, end_str))
                except Exception as e:
                    logger.warning("Season SAR fetch for raw storage failed for %s: %s", file_name, e)

                lst_celsius_snapshot = None
                vv_db_snapshot = None
                vh_db_snapshot = None
                vh_vv_ratio_snapshot = None
                vv_lin_mean = None
                vh_lin_mean = None
                if s3_bands and "S8" in s3_bands and "S9" in s3_bands:
                    try:
                        lst_arr = lst_celsius_array(s3_bands["S8"], s3_bands["S9"])
                        flat = np.asarray(lst_arr).ravel()
                        valid = np.isfinite(flat)
                        if np.any(valid):
                            lst_celsius_snapshot = float(np.nanmean(flat))
                    except Exception as e:
                        logger.warning("LST (S3) season summary for raw failed for %s: %s", file_name, e)
                if s1_bands and "VV" in s1_bands and "VH" in s1_bands:
                    try:
                        vv = np.asarray(s1_bands["VV"], dtype=np.float64)
                        vh = np.asarray(s1_bands["VH"], dtype=np.float64)
                        valid = np.isfinite(vv) & np.isfinite(vh) & (vv > 0)
                        if np.any(valid):
                            vv_lin_mean = float(np.nanmean(vv[valid]))
                            vh_lin_mean = float(np.nanmean(vh[valid]))
                            sar = compute_sar_metrics(vv, vh)
                            vv_db_snapshot = float(np.nanmean(sar["VV_dB"]))
                            vh_db_snapshot = float(np.nanmean(sar["VH_dB"]))
                            vh_vv_ratio_snapshot = float(np.nanmean(sar["vh_vv_ratio"]))
                    except Exception as e:
                        logger.warning("SAR (S1) season summary for raw failed for %s: %s", file_name, e)

                # Per analysis_date: LST + SAR for crop_indices (avoids one season mean on every row).
                unique_dates: list[str] = []
                seen_ad: set[str] = set()
                for r in rows:
                    ad = r.get("analysis_date")
                    if ad and ad not in seen_ad:
                        seen_ad.add(ad)
                        unique_dates.append(ad)

                day_metrics: dict[str, tuple[Optional[float], Optional[float], Optional[float], Optional[float]]] = {}

                def _fetch_lst_sar_day(ad: str) -> tuple[str, tuple[Optional[float], Optional[float], Optional[float], Optional[float]]]:
                    try:
                        return ad, _lst_sar_polygon_means_for_analysis_date(geojson, ad)
                    except Exception as e:
                        logger.warning("Per-day LST/SAR failed for %s date=%s: %s", file_name, ad, e)
                        return ad, (None, None, None, None)

                _nw = min(8, max(1, len(unique_dates)))
                with ThreadPoolExecutor(max_workers=_nw) as _ex:
                    for fut in as_completed([_ex.submit(_fetch_lst_sar_day, ad) for ad in unique_dates]):
                        k, tup = fut.result()
                        day_metrics[k] = tup

                for _i, _ad in enumerate(unique_dates[:3]):
                    _lst, _vv, _vh, _rat = day_metrics.get(_ad, (None, None, None, None))
                    logger.info(
                        "Per-day LST/SAR sample file=%s date=%s lst_celsius=%s vv_db=%s vh_db=%s vh_vv_ratio=%s",
                        file_name,
                        _ad,
                        _lst,
                        _vv,
                        _vh,
                        _rat,
                    )

                # Raw audit (optional): set SKIP_SATELLITE_RAW_OBSERVATION=1 to skip all satellite_raw_observation
                # writes for this file — useful if JSONB inserts leave the DB session in a bad state.
                _skip_sat_raw = os.environ.get("SKIP_SATELLITE_RAW_OBSERVATION", "").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                )
                if not _skip_sat_raw:
                    # Raw audit: keep payloads small — full Statistical API JSON + per-day rows can exceed
                    # practical limits and has caused PostgreSQL to close the connection mid-insert.
                    _full_stat_raw = os.environ.get("CROP_BATCH_FULL_STATISTICAL_RAW", "").strip().lower() in (
                        "1",
                        "true",
                        "yes",
                    )
                    if raw_s2_indices_payload is not None:
                        _store_satellite_raw_row(
                            db,
                            location_id=loc_id,
                            file_name=store_file_name,
                            source="copernicus_s2",
                            observation_date=None,
                            raw_response=(
                                {
                                    "layer": "statistical_indices_daily",
                                    "time_interval": [start_str, end_str],
                                    "response": raw_s2_indices_payload,
                                }
                                if _full_stat_raw
                                else {
                                    "layer": "statistical_indices_daily",
                                    "time_interval": [start_str, end_str],
                                    "summary_only": True,
                                    "n_daily_rows": len(rows),
                                }
                            ),
                            indices=rows if _full_stat_raw else None,
                        )
                    if raw_s2_bands_payload is not None:
                        _store_satellite_raw_row(
                            db,
                            location_id=loc_id,
                            file_name=store_file_name,
                            source="copernicus_s2",
                            observation_date=None,
                            raw_response=(
                                {
                                    "layer": "statistical_band_means_daily",
                                    "time_interval": [start_str, end_str],
                                    "response": raw_s2_bands_payload,
                                }
                                if _full_stat_raw
                                else {
                                    "layer": "statistical_band_means_daily",
                                    "time_interval": [start_str, end_str],
                                    "summary_only": True,
                                    "n_band_rows": len(rows_bands or []),
                                }
                            ),
                            bands=rows_bands if _full_stat_raw else None,
                        )

                    lst_bands_json: dict[str, Any] = {}
                    if lst_celsius_snapshot is not None:
                        lst_bands_json["lst_celsius"] = lst_celsius_snapshot
                    _store_satellite_raw_row(
                        db,
                        location_id=loc_id,
                            file_name=store_file_name,
                            source="copernicus_lst",
                        observation_date=end_date,
                        raw_response={
                            "api": "process",
                            "time_interval": [s3_start, end_str],
                            "raster_summary": _summarize_rasters_for_raw(s3_bands),
                        },
                        bands=lst_bands_json if lst_bands_json else None,
                    )

                    s1_bands_json: dict[str, Any] = {}
                    if vv_lin_mean is not None:
                        s1_bands_json["vv"] = vv_lin_mean
                    if vh_lin_mean is not None:
                        s1_bands_json["vh"] = vh_lin_mean
                    s1_indices_json: dict[str, Any] = {}
                    if vv_db_snapshot is not None:
                        s1_indices_json["VV_dB"] = vv_db_snapshot
                    if vh_db_snapshot is not None:
                        s1_indices_json["VH_dB"] = vh_db_snapshot
                    if vh_vv_ratio_snapshot is not None:
                        s1_indices_json["vh_vv_ratio"] = vh_vv_ratio_snapshot
                    _store_satellite_raw_row(
                        db,
                        location_id=loc_id,
                            file_name=store_file_name,
                            source="copernicus_s1",
                        observation_date=end_date,
                        raw_response={
                            "api": "process",
                            "time_interval": [start_str, end_str],
                            "raster_summary": _summarize_rasters_for_raw(s1_bands),
                        },
                        bands=s1_bands_json if s1_bands_json else None,
                        indices=s1_indices_json if s1_indices_json else None,
                    )
                else:
                    logger.info("SKIP_SATELLITE_RAW_OBSERVATION=1: skipping satellite_raw_observation inserts")

                inserted_this_file = 0
                skipped_this_file = 0
                file_counters = new_counters()
                for obs in rows:
                    analysis_date = obs.get("analysis_date")
                    if not analysis_date:
                        continue
                    # Unique index uq_crop_indices_location_start_variety_file uses date_start.
                    # Sentinel Statistical "interval.from" can repeat across daily buckets in some
                    # responses; the observation label day is analysis_date (used for LST/SAR keys).
                    date_start = analysis_date
                    date_end = obs.get("interval_to") or obs.get("interval_from") or analysis_date
                    if crop_indices_row_exists(
                        db, store_file_name, analysis_date, run_season_id, location_id=str(loc_id)
                    ):
                        skipped_this_file += 1
                        logger.warning("Skipping duplicate (file_name, analysis_date): %s, %s", store_file_name, analysis_date)
                        continue
                    if crop_indices_observation_exists(db, loc_id, date_start, var_id, store_file_name, run_season_id):
                        skipped_this_file += 1
                        logger.warning(
                            "Skipping duplicate (location_id, date_start, variety_id, file_name): %s, %s, %s, %s",
                            loc_id, date_start, var_id, store_file_name,
                        )
                        continue
                    ndmi_v = obs.get("NDMI")
                    bands_row = band_by_date.get(analysis_date) or {}
                    blue = bands_row.get("SENT2_B02")
                    green = bands_row.get("SENT2_B03")
                    red = bands_row.get("SENT2_B04")
                    rededge1 = bands_row.get("SENT2_B05")
                    rededge2 = bands_row.get("SENT2_B06")
                    rededge3 = bands_row.get("SENT2_B07")
                    nir = bands_row.get("SENT2_B08")
                    narrow_nir = bands_row.get("SENT2_B8A")
                    swir1 = bands_row.get("SENT2_B11")
                    swir2 = bands_row.get("SENT2_B12")
                    ndwi_gao = _ndwi_gao_from_nir_swir1(nir, swir1)
                    lst_row, vv_row, vh_row, vhvv_row = day_metrics.get(
                        analysis_date, (None, None, None, None)
                    )
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            "crop_indices row date=%s lst=%s vv_db=%s vh_db=%s (centroid lat=%s lon=%s)",
                            analysis_date,
                            lst_row,
                            vv_row,
                            vh_row,
                            lat_c,
                            lon_c,
                        )
                    new_id = insert_crop_indices(
                        db,
                        {
                            "file_name": store_file_name,
                            "analysis_date": analysis_date,
                            "location_id": loc_id,
                            "season_id": run_season_id,
                            "grower_id": grower_id,
                            "variety_id": var_id,
                            "crop_id": crop_id,
                            "crop_name": crop_name,
                            "polygon_id": polygon_id,
                            "polygon_area": polygon_area,
                            "date_start": date_start,
                            "date_end": date_end,
                            "area_acre": area_acre,
                            "distance_km": distance_km,
                            "extracted_grower": extracted_grower or grower,
                            "latitude": lat_c,
                            "longitude": lon_c,
                            "blue": blue,
                            "green": green,
                            "red": red,
                            "rededge1": rededge1,
                            "rededge2": rededge2,
                            "rededge3": rededge3,
                            "nir": nir,
                            "narrow_nir": narrow_nir,
                            "swir1": swir1,
                            "swir2": swir2,
                            "ndvi": obs.get("NDVI"),
                            "ndwi": obs.get("NDWI"),
                            "savi": obs.get("SAVI"),
                            "ndmi": ndmi_v,
                            "ndre": obs.get("NDRE"),
                            "gci": obs.get("GCI"),
                            "psri": obs.get("PSRI"),
                            "msavi": obs.get("MSAVI"),
                            "evi": obs.get("EVI"),
                            "lai": obs.get("LAI"),
                            "ndwi_gao": ndwi_gao,
                            "lst_celsius": lst_row,
                            "vv_db": vv_row,
                            "vh_db": vh_row,
                            "vh_vv_ratio": vhvv_row,
                            "grower_name": grower,
                            "variety_name": variety,
                            "field_indices_batch_tag": run_season_id,
                        },
                        counters=file_counters,
                        skip_duplicate_file_check=True,
                    )
                    if new_id > 0:
                        inserted_this_file += 1
                        print(f"  Inserted row for date: {analysis_date}")
                        # Commit each observation so long Sentinel runs are not held in one transaction
                        # and partial progress survives failures later in the file.
                        try:
                            db.commit()
                        except Exception as ce:
                            logger.exception("Commit failed after row date=%s file=%s: %s", analysis_date, file_name, ce)
                            db.rollback()
                            total_errors += 1
                            failed_keys.append(file_name)
                            break
                    else:
                        skipped_this_file += 1

                print_validation_batch_summary(file_name, file_counters)
                total_inserted += inserted_this_file
                total_skipped_duplicates += skipped_this_file
                if inserted_this_file or skipped_this_file:
                    print(f"  Rows inserted: {inserted_this_file}, skipped (duplicate): {skipped_this_file}")
                if inserted_this_file > 0:
                    logger.info("Inserted crop indices successfully for %s (%d rows)", file_name, inserted_this_file)
                    print("  Inserted crop indices")
            except Exception as e:
                logger.exception("Processing failed for %s", file_name)
                print(f"  Failed: {e}")
                total_errors += 1
                failed_keys.append(file_name)
                try:
                    db.rollback()
                except Exception:
                    pass

    if db is not None:
        try:
            db.close()
        except Exception:
            pass

    elapsed = time.perf_counter() - start_wall
    rows_per_sec = (total_inserted / elapsed) if elapsed > 0 else 0

    print("---------------------------------")
    print("BATCH PROCESS SUMMARY")
    print("---------------------------------")
    print(f"Total files (S3): {total_files}")
    print(f"Processed: {len(keys)}")
    print(f"Successfully processed: {len(keys) - len(failed_keys)}")
    print(f"Failed: {total_errors}")
    print(f"Total rows inserted: {total_inserted}")
    print(f"Skipped (already processed): {total_skipped_already_processed}")
    print(f"Skipped (duplicates): {total_skipped_duplicates}")
    print(f"Total processing time: {elapsed:.1f} s")
    print(f"Rows/sec: {rows_per_sec:.2f}")
    if failed_keys:
        print("Failed keys:")
        for k in failed_keys[:30]:
            print(f"  - {k}")
        if len(failed_keys) > 30:
            print(f"  ... and {len(failed_keys) - 30} more")
    print("---------------------------------")

    sys.exit(0 if total_errors == 0 else 1)


if __name__ == "__main__":
    main()
