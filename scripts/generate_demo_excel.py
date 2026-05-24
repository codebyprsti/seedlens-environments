#!/usr/bin/env python3
"""
Process local KML files and export one consolidated Excel with per-date rows.

Key behavior:
- Reuse existing modules for KML parsing, S2 statistical series, and index formulas.
- Resolve location only from operations.field_locations (no Google API).
- Recompute Sentinel-1/Sentinel-3 per analysis_date (no season-level reuse).
"""

from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_SCRIPT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except Exception:
    pass

import export_kml_to_excel as ex

from crop_monitoring.database.location_repository import get_field_location_row_by_centroid
from crop_monitoring.database.master_repository import get_crop_id_and_name_for_variety
from crop_monitoring.database.repository import _variety_like_match, normalize_variety_name
from crop_monitoring.database.id_resolution import fuzzy_match_variety
from crop_monitoring.index_calculator import (
    evi as idx_evi,
    gci as idx_gci,
    lai_from_evi as idx_lai_from_evi,
    msavi as idx_msavi,
    ndmi as idx_ndmi,
    ndre as idx_ndre,
    ndvi as idx_ndvi,
    psri as idx_psri,
    savi as idx_savi,
)
from crop_monitoring.kml_parser import parse_kml
from crop_monitoring.sar_calculator import compute_sar_metrics
from crop_monitoring.sentinel_client import fetch_s1_sar, fetch_s3_thermal
from crop_monitoring.statistical_client import fetch_s2_indices_timeseries
from crop_monitoring.temperature_calculator import lst_celsius
from shapely.geometry import shape

LOCAL_DIR = Path(r"C:\Users\madan\Downloads\demo_kml_files")
OUTPUT_XLSX = Path(r"C:\Users\madan\Downloads\demo_kml_output.xlsx")
SEASON_ID = "RABI_25_26"
MAX_WORKERS = 5
LOCATION_TOLERANCE_DEG = 0.00002  # ~2m

OUTPUT_COLUMNS = [
    "grower_id",
    "extracted_grower_name",
    "extracted_village",
    "polygon_id",
    "polygon_area",
    "location_id",
    "village",
    "district",
    "state",
    "mandal",
    "postcode",
    "centroid_lat",
    "centroid_lon",
    "variety_id",
    "variety_name",
    "crop_id",
    "season_id",
    "date",
    "SENT2_B02",
    "SENT2_B03",
    "SENT2_B04",
    "SENT2_B05",
    "SENT2_B06",
    "SENT2_B07",
    "SENT2_B08",
    "SENT2_B8A",
    "SENT2_B11",
    "SENT2_B12",
    "SENT1_VV",
    "SENT1_VH",
    "LST_CELSIUS",
    "NDVI",
    "NDWI",
    "NDWI_GAO",
    "NDMI",
    "EVI",
    "SAVI",
    "MSAVI",
    "GNDVI",
    "ARVI",
    "VARI",
    "PSRI",
    "LAI",
    "NDRE",
    "CCCI",
    "GCI",
]


def rabi_window() -> tuple[date, date]:
    today = date.today()
    if today.month >= 12:
        start = date(today.year, 12, 1)
    else:
        start = date(today.year - 1, 12, 1)
    return start, today


def as_scalar(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return v


def idx_scalar(fn, *values: Any) -> Optional[float]:
    vals = [as_scalar(v) for v in values]
    if any(v is None for v in vals):
        return None
    arrs = [np.array([v], dtype=np.float64) for v in vals]
    out = fn(*arrs)
    if out is None or len(out) == 0:
        return None
    v = out[0]
    return as_scalar(v)


def ndwi_gao(nir: Any, swir1: Any) -> Optional[float]:
    n = as_scalar(nir)
    s = as_scalar(swir1)
    if n is None or s is None:
        return None
    den = n + s
    if abs(den) < 1e-10:
        return None
    return (n - s) / den


def lookup_variety_crop_readonly(db, variety_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if db is None or not variety_name or not str(variety_name).strip():
        return None, None
    raw = variety_name.strip()
    search = normalize_variety_name(raw) or raw
    row = _variety_like_match(db, search)
    if row:
        vid = row[0]
        cid, _ = get_crop_id_and_name_for_variety(db, vid)
        return str(vid) if vid else None, str(cid) if cid else None
    fr = fuzzy_match_variety(db, search, threshold=90.0)
    if fr:
        vid = fr[0]
        cid, _ = get_crop_id_and_name_for_variety(db, vid)
        return str(vid) if vid else None, str(cid) if cid else None
    return None, None


def sanitize_sheet_name(name: str, max_len: int = 31) -> str:
    base = Path(name).stem
    base = "".join(ch if ch.isalnum() or ch in (" ", "_", "-") else "_" for ch in base)
    base = " ".join(base.split()).strip().replace(" ", "_")
    base = base or "Sheet"
    return base[:max_len]


def lookup_field_location_with_tolerance(
    db,
    lat: float,
    lon: float,
    *,
    tolerance: float = LOCATION_TOLERANCE_DEG,
) -> Optional[dict[str, Any]]:
    """
    First try exact 7-decimal centroid match via existing repository helper.
    If not found, fallback to nearest centroid within tolerance from field_locations.
    """
    if db is None:
        return None

    exact = get_field_location_row_by_centroid(db, lat, lon)
    if exact is not None:
        return exact

    row = db.execute(
        text(
            """
            SELECT location_id, village, district, state, mandal, postalcode
            FROM operations.field_locations
            WHERE ABS(latitude - :lat) <= :tol AND ABS(longitude - :lon) <= :tol
            ORDER BY ((latitude - :lat)*(latitude - :lat) + (longitude - :lon)*(longitude - :lon)) ASC
            LIMIT 1
            """
        ),
        {"lat": lat, "lon": lon, "tol": tolerance},
    ).fetchone()
    if not row:
        return None
    return {
        "location_id": str(row[0]) if row[0] else None,
        "village": (row[1] or "").strip() or None,
        "district": (row[2] or "").strip() or None if row[2] is not None else None,
        "state": (row[3] or "").strip() or None if row[3] is not None else None,
        "mandal": (row[4] or "").strip() or None if row[4] is not None else None,
        "postcode": (row[5] or "").strip() or None if row[5] is not None else None,
    }


def per_day_lst_sar(
    geojson: dict,
    analysis_date: str,
    *,
    s1_lookback_days: int = 12,
    s3_pad_before: int = 1,
    s3_pad_after: int = 2,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    d0 = date.fromisoformat(str(analysis_date)[:10])

    s1_from = (d0 - timedelta(days=s1_lookback_days)).isoformat()
    s1_to = (d0 + timedelta(days=1)).isoformat()
    s3_from = (d0 - timedelta(days=s3_pad_before)).isoformat()
    s3_to = (d0 + timedelta(days=s3_pad_after + 1)).isoformat()

    lst_c = None
    vv_lin = None
    vh_lin = None

    try:
        s3 = fetch_s3_thermal(geojson, (s3_from, s3_to))
        if s3 and "S8" in s3 and "S9" in s3:
            arr = lst_celsius(s3["S8"], s3["S9"])
            flat = np.asarray(arr).ravel()
            valid = np.isfinite(flat)
            if np.any(valid):
                lst_c = float(np.nanmean(flat[valid]))
    except Exception as e:
        logger.debug("LST fetch failed for %s: %s", analysis_date, e)

    try:
        s1 = fetch_s1_sar(geojson, (s1_from, s1_to))
        if s1 and "VV" in s1 and "VH" in s1:
            vv = np.asarray(s1["VV"], dtype=np.float64)
            vh = np.asarray(s1["VH"], dtype=np.float64)
            valid = np.isfinite(vv) & np.isfinite(vh) & (vv > 0)
            if np.any(valid):
                vv_lin = float(np.nanmean(vv[valid]))
                vh_lin = float(np.nanmean(vh[valid]))
    except Exception as e:
        logger.debug("SAR fetch failed for %s: %s", analysis_date, e)

    return lst_c, vv_lin, vh_lin


def process_kml(db, kml_path: Path, start_d: date, end_d: date) -> list[dict[str, Any]]:
    geojson, meta = parse_kml(kml_path)
    geom = shape(geojson)
    centroid = geom.centroid
    lat = round(float(centroid.y), 7)
    lon = round(float(centroid.x), 7)

    fl = lookup_field_location_with_tolerance(db, lat, lon) if db is not None else None
    if fl is None:
        logger.info("Location not found in field_locations for %s (%.7f, %.7f)", kml_path.name, lat, lon)
    else:
        logger.info("Location found in field_locations for %s -> %s", kml_path.name, fl.get("location_id"))

    file_name = kml_path.name
    extracted_grower_name = meta.get("extracted_grower")
    extracted_village = meta.get("village_name")
    polygon_id = ex.generate_polygon_id(file_name, kml_path)
    polygon_area = ex.calculate_area_hectares(geojson)

    variety_name = "USRH24"
    variety_id, crop_id = lookup_variety_crop_readonly(db, variety_name)

    start_str = start_d.isoformat()
    end_str = end_d.isoformat()

    idx_rows = fetch_s2_indices_timeseries(geojson, start_str, end_str, maxcc=20)
    band_rows = ex.fetch_s2_band_means_timeseries(geojson, start_str, end_str, maxcc=20)
    bands_by_date = {r.get("analysis_date"): r for r in band_rows if r.get("analysis_date")}

    dates = []
    seen = set()
    for r in idx_rows:
        ad = r.get("analysis_date")
        if ad and ad not in seen:
            seen.add(ad)
            dates.append(ad)

    day_sensors: dict[str, tuple[Optional[float], Optional[float], Optional[float]]] = {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(dates)))) as ex_pool:
        futures = {ex_pool.submit(per_day_lst_sar, geojson, d): d for d in dates}
        for fut in as_completed(futures):
            d = futures[fut]
            try:
                day_sensors[d] = fut.result()
            except Exception as e:
                logger.warning("Per-date S1/S3 failed %s %s: %s", file_name, d, e)
                day_sensors[d] = (None, None, None)

    out_rows: list[dict[str, Any]] = []
    for obs in idx_rows:
        ad = obs.get("analysis_date")
        if not ad:
            continue
        b = bands_by_date.get(ad, {})

        blue = b.get("SENT2_B02")
        green = b.get("SENT2_B03")
        red = b.get("SENT2_B04")
        rededge1 = b.get("SENT2_B05")
        rededge2 = b.get("SENT2_B06")
        rededge3 = b.get("SENT2_B07")
        nir = b.get("SENT2_B08")
        narrow_nir = b.get("SENT2_B8A")
        swir1 = b.get("SENT2_B11")
        swir2 = b.get("SENT2_B12")

        ndvi = as_scalar(obs.get("NDVI")) or idx_scalar(idx_ndvi, nir, red)
        ndwi = as_scalar(obs.get("NDWI"))
        ndwi_gao_v = ndwi_gao(nir, swir1)
        ndmi = as_scalar(obs.get("NDMI")) or idx_scalar(idx_ndmi, nir, swir1)
        evi = as_scalar(obs.get("EVI")) or idx_scalar(idx_evi, nir, red, blue)
        savi = as_scalar(obs.get("SAVI")) or idx_scalar(idx_savi, nir, red)
        msavi = as_scalar(obs.get("MSAVI")) or idx_scalar(idx_msavi, nir, red)
        gndvi = ex.compute_gndvi(nir, green)
        arvi = ex.compute_arvi(nir, red, blue, gamma=1.0)
        vari = ex.compute_vari(green, red, blue)
        psri = as_scalar(obs.get("PSRI")) or idx_scalar(idx_psri, red, green, nir)
        lai = idx_scalar(idx_lai_from_evi, evi) if evi is not None else None
        ndre = as_scalar(obs.get("NDRE")) or idx_scalar(idx_ndre, nir, rededge1)
        ccci = ex.compute_ccci(ndre, ndvi)
        gci = as_scalar(obs.get("GCI")) or idx_scalar(idx_gci, nir, green)

        lst_c, sent1_vv, sent1_vh = day_sensors.get(ad, (None, None, None))

        row = {
            "grower_id": None,
            "extracted_grower_name": extracted_grower_name,
            "extracted_village": extracted_village,
            "polygon_id": polygon_id,
            "polygon_area": polygon_area,
            "location_id": fl.get("location_id") if fl else None,
            "village": fl.get("village") if fl else None,
            "district": fl.get("district") if fl else None,
            "state": fl.get("state") if fl else None,
            "mandal": fl.get("mandal") if fl else None,
            "postcode": fl.get("postcode") if fl else None,
            "centroid_lat": lat,
            "centroid_lon": lon,
            "variety_id": variety_id,
            "variety_name": variety_name,
            "crop_id": crop_id,
            "season_id": SEASON_ID,
            "date": ad,
            "SENT2_B02": blue,
            "SENT2_B03": green,
            "SENT2_B04": red,
            "SENT2_B05": rededge1,
            "SENT2_B06": rededge2,
            "SENT2_B07": rededge3,
            "SENT2_B08": nir,
            "SENT2_B8A": narrow_nir,
            "SENT2_B11": swir1,
            "SENT2_B12": swir2,
            "SENT1_VV": sent1_vv,
            "SENT1_VH": sent1_vh,
            "LST_CELSIUS": lst_c,
            "NDVI": ndvi,
            "NDWI": ndwi,
            "NDWI_GAO": ndwi_gao_v,
            "NDMI": ndmi,
            "EVI": evi,
            "SAVI": savi,
            "MSAVI": msavi,
            "GNDVI": gndvi,
            "ARVI": arvi,
            "VARI": vari,
            "PSRI": psri,
            "LAI": lai,
            "NDRE": ndre,
            "CCCI": ccci,
            "GCI": gci,
        }
        if row["location_id"] is None:
            logger.debug(
                "Missing location_id centroid_lat=%.7f centroid_lon=%.7f polygon_id=%s",
                lat,
                lon,
                polygon_id,
            )
        out_rows.append(row)
    return out_rows


def main() -> int:
    if not LOCAL_DIR.is_dir():
        logger.error("Input directory not found: %s", LOCAL_DIR)
        return 1

    kmls = sorted([p for p in LOCAL_DIR.rglob("*.kml") if p.is_file()], key=lambda p: str(p).lower())
    if not kmls:
        logger.error("No KML files found under: %s", LOCAL_DIR)
        return 1

    try:
        from core.db import SessionLocal
        from sqlalchemy import text

        db = SessionLocal()
        db.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("Database unavailable: %s", e)
        return 1

    start_d, end_d = rabi_window()
    logger.info("Processing %d KML files from %s", len(kmls), LOCAL_DIR)
    logger.info("Date window: %s -> %s", start_d, end_d)

    per_kml_rows: dict[str, list[dict[str, Any]]] = {}
    try:
        for i, p in enumerate(kmls, start=1):
            logger.info("[%d/%d] %s", i, len(kmls), p.name)
            try:
                per_kml_rows[p.name] = process_kml(db, p, start_d, end_d)
            except Exception as e:
                logger.exception("Failed processing %s: %s", p.name, e)
                per_kml_rows[p.name] = []
    finally:
        try:
            db.close()
        except Exception:
            pass

    OUTPUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        used_names: set[str] = set()
        total_rows = 0
        for kml_name, rows in per_kml_rows.items():
            df = pd.DataFrame(rows)
            for c in OUTPUT_COLUMNS:
                if c not in df.columns:
                    df[c] = None
            df = df[OUTPUT_COLUMNS]
            total_rows += len(df)

            base_sheet = sanitize_sheet_name(kml_name)
            sheet = base_sheet
            n = 1
            while sheet in used_names:
                suffix = f"_{n}"
                sheet = f"{base_sheet[: max(0, 31 - len(suffix))]}{suffix}"
                n += 1
            used_names.add(sheet)

            df.to_excel(writer, sheet_name=sheet, index=False)

            # Bonus: freeze header and auto-fit basic column widths
            ws = writer.sheets[sheet]
            ws.freeze_panes = "A2"
            for idx, col in enumerate(df.columns, start=1):
                col_vals = [str(col)]
                if not df.empty:
                    sample = df[col].astype(str).head(200)
                    col_vals.extend(sample.tolist())
                width = min(50, max(len(v) for v in col_vals) + 2)
                ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width

    logger.info("Wrote Excel: %s (rows=%d, sheets=%d)", OUTPUT_XLSX, total_rows, len(per_kml_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
