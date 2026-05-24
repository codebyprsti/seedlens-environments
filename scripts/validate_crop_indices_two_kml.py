#!/usr/bin/env python3
"""
Validate crop_indices pipeline for exactly two KML files: fetch Sentinel data, resolve
location from operations.field_locations (centroid only), write Excel — NO inserts, NO Google API.

Variety/crop IDs are read-only lookups (no new variety rows).

Usage:
  python scripts/validate_crop_indices_two_kml.py ^
    --local-dir "C:\\Users\\madan\\Downloads\\demo_kml_files" ^
    --output "C:\\Users\\madan\\ENVIRONMENTS\\validation_2_files.xlsx"

  # Next two KMLs (sorted), append sheets to same workbook:
  python scripts/validate_crop_indices_two_kml.py --skip 2 --limit 2 --append --output "C:\\Users\\madan\\ENVIRONMENTS\\validation_2_files.xlsx"
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

_script_dir = Path(__file__).resolve().parent
_root = _script_dir.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_script_dir))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass

# Reuse export helpers (bands + index formulas)
import export_kml_to_excel as ex

from crop_monitoring.kml_parser import parse_kml
from crop_monitoring.statistical_client import fetch_s2_indices_timeseries
from crop_monitoring.database.location_repository import get_field_location_row_by_centroid
from crop_monitoring.metadata_resolver import resolve_metadata
from crop_monitoring.database.repository import _variety_like_match, normalize_variety_name
from crop_monitoring.database.id_resolution import fuzzy_match_variety
from crop_monitoring.database.master_repository import get_crop_id_and_name_for_variety
from crop_monitoring.sentinel_client import fetch_s3_thermal, fetch_s1_sar
from crop_monitoring.temperature_calculator import lst_celsius as lst_celsius_array
from crop_monitoring.sar_calculator import compute_sar_metrics
from shapely.geometry import shape

DEFAULT_SEASON_ID = "RABI_25_26"

# Exact output column order (matches user spec; names align with operations.crop_indices)
OUTPUT_COLUMNS = [
    "location_id",
    "grower_id",
    "variety_id",
    "crop_id",
    "season_id",
    "polygon_id",
    "polygon_area",
    "centroid_lat",
    "centroid_lon",
    "village",
    "district",
    "state",
    "mandal",
    "postcode",
    "extracted_village",
    "extracted_grower",
    "analysis_date",
    "blue",
    "green",
    "red",
    "rededge1",
    "rededge2",
    "rededge3",
    "nir",
    "narrow_nir",
    "swir1",
    "swir2",
    "vv_db",
    "vh_db",
    "lst_celsius",
    "ndvi",
    "ndwi",
    "ndwi_gao",
    "ndmi",
    "evi",
    "savi",
    "msavi",
    "gndvi",
    "arvi",
    "vari",
    "psri",
    "lai",
    "ndre",
    "ccci",
    "gci",
]


def get_rabi_window() -> tuple[date, date]:
    today = date.today()
    if today.month >= 12:
        start = date(today.year, 12, 1)
    else:
        start = date(today.year - 1, 12, 1)
    return start, today


def list_kml_files(local_dir: Path, *, skip: int = 0, limit: int = 2) -> list[Path]:
    """Sorted KML paths; skip first `skip` files, then take up to `limit`."""
    paths = sorted(local_dir.rglob("*.kml"), key=lambda p: str(p).lower())
    if skip > 0:
        paths = paths[skip:]
    out: list[Path] = []
    for p in paths:
        if not p.is_file():
            continue
        try:
            if p.stat().st_size == 0:
                continue
        except OSError:
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


def lookup_variety_crop_readonly(db, variety_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """LIKE + fuzzy match only — never creates varieties."""
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


def thermal_sar_snapshot_db(
    geometry_geojson: dict, start_str: str, end_str: str
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Polygon-mean LST (°C) and SAR means in dB — same idea as run_crop_analysis_s3_batch."""
    end_d = date.fromisoformat(end_str[:10])
    s3_start = (end_d - timedelta(days=10)).isoformat()
    lst_c = None
    vv_db = vh_db = None
    try:
        s3_b = fetch_s3_thermal(geometry_geojson, (s3_start, end_str))
        if s3_b and "S8" in s3_b and "S9" in s3_b:
            arr = lst_celsius_array(s3_b["S8"], s3_b["S9"])
            flat = np.asarray(arr).ravel()
            v = flat[np.isfinite(flat)]
            if v.size:
                lst_c = float(np.nanmean(v))
    except Exception as e:
        logger.warning("LST fetch failed: %s", e)
    try:
        s1 = fetch_s1_sar(geometry_geojson, (start_str, end_str))
        if s1 and "VV" in s1 and "VH" in s1:
            vv = np.asarray(s1["VV"], dtype=np.float64)
            vh = np.asarray(s1["VH"], dtype=np.float64)
            ok = np.isfinite(vv) & np.isfinite(vh) & (vv > 0)
            if np.any(ok):
                sar = compute_sar_metrics(vv, vh)
                vv_db = float(np.nanmean(sar["VV_dB"]))
                vh_db = float(np.nanmean(sar["VH_dB"]))
    except Exception as e:
        logger.warning("SAR fetch failed: %s", e)
    return lst_c, vv_db, vh_db


def band_dict_to_row(b: dict[str, Any]) -> dict[str, Optional[float]]:
    """Map SENT2_B* export columns to crop_indices-style names."""
    return {
        "blue": b.get("SENT2_B02"),
        "green": b.get("SENT2_B03"),
        "red": b.get("SENT2_B04"),
        "rededge1": b.get("SENT2_B05"),
        "rededge2": b.get("SENT2_B06"),
        "rededge3": b.get("SENT2_B07"),
        "nir": b.get("SENT2_B08"),
        "narrow_nir": b.get("SENT2_B8A"),
        "swir1": b.get("SENT2_B11"),
        "swir2": b.get("SENT2_B12"),
    }


def build_rows_for_file(
    db,
    kml_path: Path,
    start_date: date,
    end_date: date,
    season_id: str,
) -> pd.DataFrame:
    file_name = kml_path.name
    geojson, meta_kml = parse_kml(kml_path)
    placemark_name = meta_kml.get("placemark_name") or ""
    extracted_village = meta_kml.get("village_name")
    extracted_grower = meta_kml.get("extracted_grower")

    g = shape(geojson)
    centroid = g.centroid
    lon_c = round(float(centroid.x), 7)
    lat_c = round(float(centroid.y), 7)
    polygon_area = ex.calculate_area_hectares(geojson)
    polygon_id = ex.generate_polygon_id(file_name, kml_path)

    fl = get_field_location_row_by_centroid(db, lat_c, lon_c) if db is not None else None
    if db is not None and fl is None:
        logger.warning(
            "No field_locations row for centroid (%.7f, %.7f); location columns will be empty",
            lat_c,
            lon_c,
        )

    # No db_session: skips repository get_variety_id (which can INSERT). Google not used (no detected_location).
    resolved = resolve_metadata(
        kml_name=placemark_name,
        filename=file_name,
        db_session=None,
        use_llm=False,
        detected_location=None,
    )
    variety = resolved.get("variety")
    variety_id, crop_id = lookup_variety_crop_readonly(db, variety)

    loc_id = fl["location_id"] if fl else None
    village = fl.get("village") if fl else None
    district = fl.get("district") if fl else None
    state = fl.get("state") if fl else None
    mandal = fl.get("mandal") if fl else None
    postcode = fl.get("postcode") if fl else None

    start_str, end_str = start_date.isoformat(), end_date.isoformat()
    lst_c, vv_db, vh_db = thermal_sar_snapshot_db(geojson, start_str, end_str)

    rows_idx = fetch_s2_indices_timeseries(geojson, start_str, end_str, maxcc=20)
    try:
        rows_bands = ex.fetch_s2_band_means_timeseries(geojson, start_str, end_str, maxcc=20)
    except Exception as e:
        logger.warning("Band timeseries fetch failed: %s", e)
        rows_bands = []
    band_by_date = {r["analysis_date"]: r for r in rows_bands if r.get("analysis_date")}

    out_rows: list[dict[str, Any]] = []
    for obs in rows_idx:
        ad = obs.get("analysis_date")
        if not ad:
            continue
        b = band_by_date.get(ad) or {}
        bands = band_dict_to_row(b)

        red, green, blue = bands["red"], bands["green"], bands["blue"]
        nir, swir1 = bands["nir"], bands["swir1"]
        rededge1 = bands["rededge1"]

        ndmi_swir = ex.ndmi_ndwi_gao_from_nir_swir1(nir, swir1)
        ndmi_out = obs.get("NDMI")
        if ndmi_out is None:
            ndmi_out = ndmi_swir

        ndvi = obs.get("NDVI")
        ndre_val = obs.get("NDRE")
        if ndre_val is None and rededge1 is not None and nir is not None:
            ndre_val = ex.compute_ndre_from_bands(nir, rededge1)

        psri_v = obs.get("PSRI")
        if psri_v is None:
            psri_v = ex.compute_psri_red_green_nir(red, green, nir)

        row: dict[str, Any] = {
            "location_id": loc_id,
            "grower_id": None,
            "variety_id": variety_id,
            "crop_id": crop_id,
            "season_id": season_id,
            "polygon_id": polygon_id,
            "polygon_area": polygon_area,
            "centroid_lat": lat_c,
            "centroid_lon": lon_c,
            "village": village,
            "district": district,
            "state": state,
            "mandal": mandal,
            "postcode": postcode,
            "extracted_village": extracted_village,
            "extracted_grower": extracted_grower,
            "analysis_date": ad,
            **bands,
            "vv_db": vv_db,
            "vh_db": vh_db,
            "lst_celsius": lst_c,
            "ndvi": ndvi,
            "ndwi": obs.get("NDWI"),
            "ndwi_gao": ndmi_swir,
            "ndmi": ndmi_out,
            "evi": obs.get("EVI"),
            "savi": obs.get("SAVI"),
            "msavi": obs.get("MSAVI"),
            "gndvi": ex.compute_gndvi(nir, green),
            "arvi": ex.compute_arvi(nir, red, blue, gamma=1.0),
            "vari": ex.compute_vari(green, red, blue),
            "psri": psri_v,
            "lai": ex.compute_lai_from_evi(obs.get("EVI")),
            "ndre": ndre_val,
            "ccci": ex.compute_ccci(ndre_val, ndvi),
            "gci": obs.get("GCI"),
        }
        out_rows.append(row)

    df = pd.DataFrame(out_rows)
    for c in OUTPUT_COLUMNS:
        if c not in df.columns:
            df[c] = None
    return df[OUTPUT_COLUMNS]


def main() -> None:
    p = argparse.ArgumentParser(description="Validate 2 KML -> crop_indices-shaped Excel (no DB insert, no Google).")
    p.add_argument(
        "--local-dir",
        type=str,
        default=r"C:\Users\madan\Downloads\demo_kml_files",
        help="Directory containing KML files (recursive).",
    )
    p.add_argument(
        "--output",
        type=str,
        default=r"C:\Users\madan\ENVIRONMENTS\validation_2_files.xlsx",
        help="Excel output path.",
    )
    p.add_argument("--limit", type=int, default=2, help="Number of KML files to process (default: 2).")
    p.add_argument(
        "--skip",
        type=int,
        default=0,
        help="Skip this many KML files (sorted order) before taking --limit. Use 2 after processing files 1–2.",
    )
    p.add_argument(
        "--append",
        action="store_true",
        help="Add sheets to existing Excel (--output); do not delete current sheets. Requires pandas 2+ openpyxl.",
    )
    p.add_argument("--season-id", type=str, default=DEFAULT_SEASON_ID, metavar="ID", help="Season id column value.")
    args = p.parse_args()

    local_root = Path(args.local_dir).resolve()
    if not local_root.is_dir():
        logger.error("Not a directory: %s", local_root)
        sys.exit(1)

    kmls = list_kml_files(local_root, skip=args.skip, limit=args.limit)
    if len(kmls) < 1:
        logger.error("No KML files under %s", local_root)
        sys.exit(1)
    if len(kmls) < args.limit:
        logger.warning("Only %d KML file(s) found (requested %d)", len(kmls), args.limit)

    try:
        from core.db import SessionLocal
        from sqlalchemy import text

        db = SessionLocal()
        db.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("Database unavailable: %s", e)
        sys.exit(1)

    start_d, end_d = get_rabi_window()
    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        append_mode = bool(args.append) and out_path.is_file()
        writer_kw: dict[str, Any] = {"engine": "openpyxl"}
        if append_mode:
            writer_kw["mode"] = "a"
            writer_kw["if_sheet_exists"] = "replace"
        else:
            writer_kw["mode"] = "w"

        with pd.ExcelWriter(out_path, **writer_kw) as writer:
            for i, kml_path in enumerate(kmls, start=1):
                logger.info("Processing %d/%d", i, len(kmls))
                sheet = ex.sanitize_sheet_name(kml_path.name)
                df = build_rows_for_file(db, kml_path, start_d, end_d, args.season_id)
                df.to_excel(writer, sheet_name=sheet, index=False)
        action = "Appended to" if append_mode else "Wrote"
        logger.info("%s %s (%d sheet(s) this run)", action, out_path, len(kmls))
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
