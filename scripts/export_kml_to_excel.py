#!/usr/bin/env python3
"""
Export KML crop analytics to Excel — one sheet per KML.

Defaults: /opt/prsti/kml_files, first 25 KMLs, last 90 days, /opt/prsti/kml_output_25.xlsx.

Bands (prefixed only): SENT2_B02…SENT2_B8A, SENT1_VV, SENT1_VH, LST_CELSIUS.
Indices: NDVI, NDWI, … (fixed set + any extra from Statistical API, e.g. GCI).
grower_id is always NULL. Metadata includes extracted_grower (filename heuristic), extracted_village, polygon_id, area, location, variety, crop, season, date.

Override: --local-dir, --limit, --output, --start/--end, or --recent-days N.

Usage:
  python scripts/export_kml_to_excel.py
  python scripts/export_kml_to_excel.py --recent-days 90 --limit 25
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

_EPS = 1e-10
_NDVI_EPS = 1e-6  # for CCCI denominator

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

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

VARIETY_ID = "VR_1001"
VARIETY_NAME = "USRH24"
CROP_ID = "CR_001"
SEASON_ID = "RABI_25_26"
LAI_A, LAI_B = 3.618, -0.118

# Sentinel-2 L2A bands (10m VNIR; SWIR B11/B12 resampled by Hub to query resolution)
SENT2_BAND_IDS = ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"]
SENT2_COLUMNS = [f"SENT2_{b}" for b in SENT2_BAND_IDS]

METADATA_COLUMNS = [
    "grower_id",
    "extracted_grower",
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
]

SENSOR_COLUMNS = ["SENT1_VV", "SENT1_VH", "LST_CELSIUS"]

# Required index columns (order); API may supply more — appended dynamically
REQUIRED_INDEX_COLUMNS = [
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
]

def build_output_columns(extra_indices: list[str]) -> list[str]:
    """Stable column order: metadata, SENT2_*, SENT1_*, LST, fixed indices, extra indices."""
    seen: set[str] = set()
    extras = []
    for x in sorted(extra_indices):
        if x not in REQUIRED_INDEX_COLUMNS and x not in seen:
            seen.add(x)
            extras.append(x)
    return (
        METADATA_COLUMNS
        + SENT2_COLUMNS
        + SENSOR_COLUMNS
        + REQUIRED_INDEX_COLUMNS
        + extras
    )


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


def get_kml_files(local_dir: str | Path, limit: int = 25) -> list[Path]:
    """Sorted .kml paths under local_dir (recursive), capped at limit."""
    root = Path(local_dir).resolve()
    if not root.is_dir():
        return []
    paths = sorted(root.rglob("*.kml"), key=lambda p: str(p).lower())
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


def extract_grower_name(filename: str) -> str:
    """
    Parse a grower-oriented substring from KML filename for DB LIKE search.
    e.g. 'Village X USRH-24 Grower Name 2.kml' -> 'Grower Name 2'.
    """
    stem = Path(filename).stem.strip()
    for pat in (
        re.compile(r"\s+USRH[-_ ]?24\s+", re.I),
        re.compile(r"\s+USRH[-_ ]?26\s+", re.I),
    ):
        parts = pat.split(stem, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()
    tokens = stem.replace(".", " ").split()
    if len(tokens) >= 3:
        return " ".join(tokens[2:])
    return stem


def extract_village_from_filename(filename: str) -> str:
    """
    Locality / village-like segment from filename: text before USRH-24 / USRH-26 (same anchor as grower).
    e.g. 'Villa Laxmikant Pradhan Usrh24 Kashi Pradhan 2' -> 'Villa Laxmikant Pradhan'
    If no USRH marker, use first whitespace-delimited token (or full stem if single token).
    """
    stem = Path(filename).stem.strip()
    for pat in (
        re.compile(r"\s+USRH[-_ ]?24\s+", re.I),
        re.compile(r"\s+USRH[-_ ]?26\s+", re.I),
    ):
        parts = pat.split(stem, maxsplit=1)
        if parts and parts[0].strip():
            return parts[0].strip()
    tokens = stem.replace(".", " ").split()
    if not tokens:
        return stem
    return tokens[0] if len(tokens) == 1 else " ".join(tokens[: min(2, len(tokens))])


def fetch_location_from_db(
    db: Any, latitude: float, longitude: float
) -> dict[str, Any]:
    """
    Match operations.field_locations by centroid at 7 decimal places (same as pipeline / get_field_location_row_by_centroid).
    Returns location_id, village, district, state, mandal, postcode; empty strings if no row.
    """
    out: dict[str, Any] = {
        "location_id": None,
        "village": None,
        "district": None,
        "state": None,
        "mandal": None,
        "postcode": None,
    }
    if db is None:
        return out
    from sqlalchemy import text

    lat = round(float(latitude), 7)
    lon = round(float(longitude), 7)
    try:
        row = db.execute(
            text(
                """
                SELECT location_id, village, district, state, mandal, postalcode
                FROM operations.field_locations
                WHERE ROUND(CAST(latitude AS NUMERIC), 7) = ROUND(CAST(:lat AS NUMERIC), 7)
                  AND ROUND(CAST(longitude AS NUMERIC), 7) = ROUND(CAST(:lon AS NUMERIC), 7)
                LIMIT 1
                """
            ),
            {"lat": lat, "lon": lon},
        ).fetchone()
        if row:
            out["location_id"] = str(row[0]) if row[0] else None
            out["village"] = (row[1] or "").strip() or None
            out["district"] = (row[2] or "").strip() or None
            out["state"] = (row[3] or "").strip() or None
            out["mandal"] = (row[4] or "").strip() or None
            out["postcode"] = (row[5] or "").strip() or None
    except Exception as e:
        logger.warning("field_locations lookup failed: %s", e)
    return out


def fetch_grower_id_from_db(db: Any, search_name: str) -> Optional[str]:
    """
    Lookup grower_id from operations.growers:
      WHERE LOWER(TRIM(grower_name)) LIKE '%<parsed_from_filename>%'
    Tries full parsed name, then shorter patterns. Returns None if no row.
    """
    if db is None or not search_name or not str(search_name).strip():
        return None
    from sqlalchemy import text

    name = " ".join(str(search_name).split()).strip()
    candidates = [name.lower()]
    words = name.split()
    if len(words) > 2:
        candidates.append(" ".join(words[:-1]).lower())
    if len(words) > 1:
        candidates.append(words[-1].lower())
    seen: set[str] = set()
    for cand in candidates:
        if len(cand) < 2 or cand in seen:
            continue
        seen.add(cand)
        pattern = f"%{cand}%"
        try:
            row = db.execute(
                text(
                    """
                    SELECT grower_id FROM operations.growers
                    WHERE LOWER(TRIM(grower_name)) LIKE :p
                    LIMIT 1
                    """
                ),
                {"p": pattern},
            ).fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception as e:
            logger.debug("grower lookup failed for pattern %r: %s", pattern, e)
    return None


def generate_polygon_id(filename: str, file_path: Optional[Path] = None) -> str:
    """Unique polygon id from resolved path + name (SHA256 prefix)."""
    key = str(Path(file_path).resolve()) if file_path else filename
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"POLY_{h.upper()}"


def calculate_area_hectares(geometry_geojson: dict) -> float:
    """Polygon area in hectares (WGS84 -> Web Mercator), same idea as batch pipeline."""
    try:
        import pyproj
        from shapely.geometry import shape
        from shapely.ops import transform

        g = shape(geometry_geojson)
        proj = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        gm = transform(proj.transform, g)
        return float(gm.area / 10_000.0)
    except Exception:
        return float("nan")


def _get_stat_config():
    from crop_monitoring.statistical_client import _get_config

    return _get_config()


def fetch_s2_band_means_timeseries(
    geometry_geojson: dict,
    start_date: str,
    end_date: str,
    *,
    maxcc: float = 20,
    resolution: int = 10,
) -> list[dict[str, Any]]:
    from sentinelhub import CRS, DataCollection, Geometry, SentinelHubStatistical

    config = _get_stat_config()
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
    input_data = SentinelHubStatistical.input_data(dc, maxcc=maxcc_01)
    request = SentinelHubStatistical(
        aggregation=aggregation,
        input_data=[input_data],
        geometry=geom,
        config=config,
    )
    stats_list = request.get_data()
    if not stats_list:
        return []
    data = stats_list[0].get("data") or []
    rows: list[dict[str, Any]] = []
    for item in data:
        interval = item.get("interval") or {}
        from_ts = interval.get("from", "")
        to_ts = interval.get("to", "")
        analysis_date = to_ts[:10] if len(to_ts) >= 10 else (from_ts[:10] if len(from_ts) >= 10 else None)
        out = item.get("outputs") or {}
        ob = out.get("bands") or {}
        bands_out = ob.get("bands") if isinstance(ob.get("bands"), dict) else ob
        row: dict[str, Any] = {"analysis_date": analysis_date}
        for i, bid in enumerate(SENT2_BAND_IDS):
            col = f"SENT2_{bid}"
            bd = bands_out.get(f"B{i}") or bands_out.get(str(i))
            if bd and isinstance(bd.get("stats"), dict):
                m = bd["stats"].get("mean")
                row[col] = float(m) if m is not None else None
            else:
                row[col] = None
        rows.append(row)
    return rows


def sanitize_sheet_name(name: str, max_len: int = 31) -> str:
    base = Path(name).stem
    base = re.sub(r"[\[\]:*?/\\]", "_", base)
    base = base.strip() or "Sheet"
    if len(base) > max_len:
        base = base[: max_len - 3] + "..."
    return base


def default_dec1_to_march18() -> tuple[date, date]:
    """RABI_25_26: one row per calendar day from 1 Dec through 18 Mar."""
    return date(2025, 12, 1), date(2026, 3, 18)


def last_n_days_range(days: int = 90) -> tuple[date, date]:
    """Optional: end = today, start = today - days."""
    end = date.today()
    return end - timedelta(days=days), end


def compute_psri_red_green_nir(red: Any, green: Any, nir: Any) -> Optional[float]:
    """PSRI = (Red - Green) / NIR (B4, B3, B8); matches statistical_client / index_calculator."""
    if red is None or green is None or nir is None:
        return None
    try:
        r, g, n = float(red), float(green), float(nir)
        if not (np.isfinite(r) and np.isfinite(g) and np.isfinite(n)):
            return None
        return _safe_div(r - g, n)
    except (TypeError, ValueError):
        return None


def compute_lai_from_evi(evi_val: Any) -> Optional[float]:
    """LAI = 3.618 * EVI - 0.118 (EVI from B8, B4, B2); not from NDVI."""
    if evi_val is None:
        return None
    try:
        v = float(evi_val)
        if not np.isfinite(v):
            return None
        return LAI_A * v + LAI_B
    except (TypeError, ValueError):
        return None


def fetch_lst_and_sar_linear(
    geometry_geojson: dict, start_str: str, end_str: str
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Polygon-mean lst_celsius (S3), mean VV/VH linear backscatter (S1)."""
    import numpy as np
    from crop_monitoring.sentinel_client import fetch_s3_thermal, fetch_s1_sar
    from crop_monitoring.temperature_calculator import lst_celsius as lst_c_arr

    end_d = date.fromisoformat(end_str[:10])
    s3_start = (end_d - timedelta(days=10)).isoformat()
    lst_c = None
    vv_m = vh_m = None
    try:
        s3 = fetch_s3_thermal(geometry_geojson, (s3_start, end_str))
        if s3 and "S8" in s3 and "S9" in s3:
            arr = lst_c_arr(s3["S8"], s3["S9"])
            flat = np.asarray(arr).ravel()
            v = flat[np.isfinite(flat)]
            if v.size:
                lst_c = float(np.nanmean(v))
    except Exception as e:
        logger.warning("LST fetch failed: %s", e)
    try:
        s1 = fetch_s1_sar(geometry_geojson, (start_str, end_str))
        if s1 and "VV" in s1 and "VH" in s1:
            vv_a = np.asarray(s1["VV"], dtype=np.float64)
            vh_a = np.asarray(s1["VH"], dtype=np.float64)
            ok = np.isfinite(vv_a) & np.isfinite(vh_a) & (vv_a > 0)
            if np.any(ok):
                vv_m = float(np.nanmean(vv_a[ok]))
                vh_m = float(np.nanmean(vh_a[ok]))
    except Exception as e:
        logger.warning("SAR fetch failed: %s", e)
    return lst_c, vv_m, vh_m


def _safe_div(num: Any, den: Any, eps: float = _EPS) -> Optional[float]:
    """Finite numerator/denominator, |den| > eps."""
    try:
        n = float(num)
        d = float(den)
        if not np.isfinite(n) or not np.isfinite(d) or abs(d) < eps:
            return None
        return n / d
    except (TypeError, ValueError):
        return None


def compute_gndvi(nir: Any, green: Any) -> Optional[float]:
    """GNDVI = (NIR - Green) / (NIR + Green)."""
    if nir is None or green is None:
        return None
    try:
        n, g = float(nir), float(green)
        if not (np.isfinite(n) and np.isfinite(g)):
            return None
        return _safe_div(n - g, n + g)
    except (TypeError, ValueError):
        return None


def compute_arvi(nir: Any, red: Any, blue: Any, gamma: float = 1.0) -> Optional[float]:
    """
    ARVI (Kaufman & Tanré): RB = Red - γ*(Blue - Red) = (1+γ)*Red - γ*Blue; γ=1 → 2*Red - Blue.
    ARVI = (NIR - RB) / (NIR + RB).
    """
    if nir is None or red is None or blue is None:
        return None
    try:
        n, r, b = float(nir), float(red), float(blue)
        if not (np.isfinite(n) and np.isfinite(r) and np.isfinite(b)):
            return None
        rb = (1.0 + gamma) * r - gamma * b
        return _safe_div(n - rb, n + rb)
    except (TypeError, ValueError):
        return None


def compute_vari(green: Any, red: Any, blue: Any) -> Optional[float]:
    """VARI = (Green - Red) / (Green + Red - Blue)."""
    if green is None or red is None or blue is None:
        return None
    try:
        g, r, b = float(green), float(red), float(blue)
        if not (np.isfinite(g) and np.isfinite(r) and np.isfinite(b)):
            return None
        den = g + r - b
        return _safe_div(g - r, den)
    except (TypeError, ValueError):
        return None


def compute_ndre_from_bands(nir: Any, red_edge: Any) -> Optional[float]:
    """NDRE = (NIR - RedEdge) / (NIR + RedEdge)."""
    if nir is None or red_edge is None:
        return None
    try:
        n, re = float(nir), float(red_edge)
        if not (np.isfinite(n) and np.isfinite(re)):
            return None
        return _safe_div(n - re, n + re)
    except (TypeError, ValueError):
        return None


def compute_ccci(ndre: Any, ndvi: Any) -> Optional[float]:
    """CCCI = NDRE / NDVI (canopy chlorophyll vs greenness); NDVI magnitude guard."""
    if ndre is None or ndvi is None:
        return None
    try:
        nv = float(ndvi)
        if not np.isfinite(nv) or abs(nv) < _NDVI_EPS:
            return None
        return _safe_div(float(ndre), nv)
    except (TypeError, ValueError):
        return None


def ndmi_ndwi_gao_from_nir_swir1(nir: Any, swir1: Any) -> Optional[float]:
    """NDMI and NDWI (Gao): (NIR - SWIR1) / (NIR + SWIR1) with safe division."""
    if nir is None or swir1 is None:
        return None
    try:
        n, s = float(nir), float(swir1)
        if not (np.isfinite(n) and np.isfinite(s)):
            return None
        return _safe_div(n - s, n + s)
    except (TypeError, ValueError):
        return None


def round_numeric_columns(df: pd.DataFrame, decimals: int = 4) -> pd.DataFrame:
    """Round float columns; leave strings/object as-is."""
    skip = set(METADATA_COLUMNS)
    for c in df.columns:
        if c in skip:
            continue

        def _r(x: Any) -> Any:
            if x is None or (isinstance(x, float) and np.isnan(x)):
                return None
            try:
                v = float(x)
                if not np.isfinite(v):
                    return None
                return round(v, decimals)
            except (TypeError, ValueError):
                return x

        df[c] = df[c].map(_r)
    return df


def create_dataframe(
    daily_rows: list[dict[str, Any]],
    polygon_id: str,
    polygon_area: float,
    location_meta: dict[str, Any],
    extra_indices: list[str],
    extracted_grower: str,
    extracted_village: str,
) -> pd.DataFrame:
    """Attach constant metadata; grower_id always NULL per export policy."""
    cols = build_output_columns(extra_indices)
    if not daily_rows:
        return pd.DataFrame(columns=cols)
    pa = round(float(polygon_area), 4) if np.isfinite(float(polygon_area)) else polygon_area
    meta = {
        "grower_id": None,
        "extracted_grower": extracted_grower or None,
        "extracted_village": extracted_village or None,
        "polygon_id": polygon_id,
        "polygon_area": pa,
        "location_id": location_meta.get("location_id"),
        "village": location_meta.get("village"),
        "district": location_meta.get("district"),
        "state": location_meta.get("state"),
        "mandal": location_meta.get("mandal"),
        "postcode": location_meta.get("postcode"),
        "centroid_lat": location_meta.get("centroid_lat"),
        "centroid_lon": location_meta.get("centroid_lon"),
        "variety_id": VARIETY_ID,
        "variety_name": VARIETY_NAME,
        "crop_id": CROP_ID,
        "season_id": SEASON_ID,
    }
    out_rows = []
    for r in daily_rows:
        out_rows.append({**meta, **r})
    df = pd.DataFrame(out_rows)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    return round_numeric_columns(df, 4)


def compute_indices_for_row(
    obs: dict[str, Any],
    b: dict[str, Any],
    lst_c: Optional[float],
    vv_m: Optional[float],
    vh_m: Optional[float],
    extra_indices_acc: set[str],
) -> dict[str, Any]:
    """One row: prefixed bands, SAR/LST, indices; dynamic API-only indices (e.g. GCI)."""
    red = b.get("SENT2_B04")
    nir = b.get("SENT2_B08")
    green = b.get("SENT2_B03")
    blue = b.get("SENT2_B02")
    red_edge = b.get("SENT2_B05")
    swir1 = b.get("SENT2_B11")
    ndmi_swir = ndmi_ndwi_gao_from_nir_swir1(nir, swir1)
    ndmi_out = obs.get("NDMI")
    if ndmi_out is None:
        ndmi_out = ndmi_swir
    ndvi = obs.get("NDVI")
    ndre_val = obs.get("NDRE")
    if ndre_val is None and red_edge is not None and nir is not None:
        ndre_val = compute_ndre_from_bands(nir, red_edge)
    ad = obs.get("analysis_date")

    row: dict[str, Any] = {k: b.get(k) for k in SENT2_COLUMNS}
    row.update(
        {
            "date": ad,
            "SENT1_VV": vv_m,
            "SENT1_VH": vh_m,
            "LST_CELSIUS": lst_c,
            "NDVI": ndvi,
            "NDWI": obs.get("NDWI"),
            "NDWI_GAO": ndmi_swir,
            "NDMI": ndmi_out,
            "EVI": obs.get("EVI"),
            "SAVI": obs.get("SAVI"),
            "MSAVI": obs.get("MSAVI"),
            "GNDVI": compute_gndvi(nir, green),
            "ARVI": compute_arvi(nir, red, blue, gamma=1.0),
            "VARI": compute_vari(green, red, blue),
            "PSRI": (
                obs["PSRI"]
                if "PSRI" in obs and obs["PSRI"] is not None
                else compute_psri_red_green_nir(red, green, nir)
            ),
            "LAI": compute_lai_from_evi(obs.get("EVI")),
            "NDRE": ndre_val,
            "CCCI": compute_ccci(ndre_val, ndvi),
        }
    )
    skip_obs = frozenset(
        {"analysis_date", "interval_from", "interval_to", "LAI"}
        | set(REQUIRED_INDEX_COLUMNS)
        | set(SENT2_COLUMNS)
        | {"SENT1_VV", "SENT1_VH", "LST_CELSIUS", "date"}
    )
    for k, v in obs.items():
        if k in skip_obs or not isinstance(k, str):
            continue
        if not k.isupper():
            continue
        row[k] = v
        if k not in REQUIRED_INDEX_COLUMNS:
            extra_indices_acc.add(k)
    return row


def expand_to_calendar_rows(
    sparse_rows: list[dict[str, Any]],
    start_date: date,
    end_date: date,
    lst_c: Optional[float],
    vv_m: Optional[float],
    vh_m: Optional[float],
    extra_index_cols: list[str],
) -> list[dict[str, Any]]:
    """
    One row per calendar day. Days without S2 keep SENT2_* / indices NULL; SENT1/LST snapshot repeated.
    """
    by_date: dict[str, dict[str, Any]] = {}
    for r in sparse_rows:
        d = r.get("date")
        if d:
            by_date[str(d)[:10]] = r
    empty_ts: dict[str, Any] = {k: None for k in SENT2_COLUMNS}
    empty_ts["SENT1_VV"] = vv_m
    empty_ts["SENT1_VH"] = vh_m
    empty_ts["LST_CELSIUS"] = lst_c
    for c in REQUIRED_INDEX_COLUMNS:
        empty_ts[c] = None
    for c in extra_index_cols:
        empty_ts[c] = None
    out: list[dict[str, Any]] = []
    cur = start_date
    while cur <= end_date:
        ds = cur.isoformat()
        if ds in by_date:
            row = dict(by_date[ds])
            row["SENT1_VV"] = vv_m
            row["SENT1_VH"] = vh_m
            row["LST_CELSIUS"] = lst_c
            for c in extra_index_cols:
                if c not in row:
                    row[c] = None
            out.append(row)
        else:
            out.append({"date": ds, **empty_ts})
        cur += timedelta(days=1)
    return out


def process_kml(
    file_path: Path,
    start_date: date,
    end_date: date,
    db: Any,
) -> pd.DataFrame:
    """Load KML, resolve field_locations by centroid, fetch S2 bands + indices / S3 LST / S1 SAR."""
    from crop_monitoring.kml_parser import parse_kml
    from crop_monitoring.statistical_client import fetch_s2_indices_timeseries
    from shapely.geometry import shape

    file_name = file_path.name
    geojson, _meta = parse_kml(file_path)
    polygon_area = calculate_area_hectares(geojson)
    polygon_id = generate_polygon_id(file_name, file_path)
    c = shape(geojson).centroid
    lon_c = round(float(c.x), 7)
    lat_c = round(float(c.y), 7)
    loc = fetch_location_from_db(db, lat_c, lon_c) if db else {}
    loc = {
        **{
            "location_id": None,
            "village": None,
            "district": None,
            "state": None,
            "mandal": None,
            "postcode": None,
        },
        **loc,
        "centroid_lat": lat_c,
        "centroid_lon": lon_c,
    }
    if db and not loc.get("location_id"):
        logger.warning(
            "No field_locations row for centroid (%.5f, %.5f): %s",
            lat_c,
            lon_c,
            file_name,
        )

    start_str, end_str = start_date.isoformat(), end_date.isoformat()
    lst_c, vv_m, vh_m = fetch_lst_and_sar_linear(geojson, start_str, end_str)

    rows_idx = fetch_s2_indices_timeseries(geojson, start_str, end_str, maxcc=20)
    try:
        rows_bands = fetch_s2_band_means_timeseries(
            geojson, start_str, end_str, maxcc=20
        )
    except Exception as e:
        logger.warning("SWIR bands not available")
        logger.debug("Band timeseries fetch failed: %s", e)
        rows_bands = []
    band_by_date = {r["analysis_date"]: r for r in rows_bands if r.get("analysis_date")}
    if not rows_bands or not any(
        r.get("SENT2_B11") is not None or r.get("SENT2_B12") is not None
        for r in rows_bands
    ):
        logger.warning("SWIR bands not available")
    n_s2 = 0
    for r in rows_bands:
        n_s2 = max(n_s2, sum(1 for k in SENT2_COLUMNS if r.get(k) is not None))
    n_s1 = (1 if vv_m is not None else 0) + (1 if vh_m is not None else 0)
    print(f"Extracted SENT2 bands: {n_s2}")
    print(f"Extracted SENT1 bands: {n_s1}")

    extra_acc: set[str] = set()
    daily_sparse: list[dict[str, Any]] = []
    for obs in rows_idx:
        if not obs.get("analysis_date"):
            continue
        b = band_by_date.get(obs["analysis_date"], {f"SENT2_{bid}": None for bid in SENT2_BAND_IDS})
        daily_sparse.append(
            compute_indices_for_row(obs, b, lst_c, vv_m, vh_m, extra_acc)
        )

    extra_list = sorted(extra_acc)
    daily = expand_to_calendar_rows(
        daily_sparse, start_date, end_date, lst_c, vv_m, vh_m, extra_list
    )
    eg = extract_grower_name(file_name)
    ev = extract_village_from_filename(file_name)
    return create_dataframe(daily, polygon_id, polygon_area, loc, extra_list, eg, ev)


def write_to_excel(dataframes: dict[str, pd.DataFrame], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for base_name, df in dataframes.items():
            sheet = sanitize_sheet_name(base_name)
            orig = sheet
            n = 1
            while sheet in used:
                suf = f"_{n}"
                sheet = (orig[: 31 - len(suf)] + suf) if len(orig) + len(suf) > 31 else orig + suf
                sheet = sheet[:31]
                n += 1
            used.add(sheet)
            if df.empty:
                pd.DataFrame({"message": ["No data or invalid geometry"]}).to_excel(
                    writer, sheet_name=sheet, index=False
                )
            else:
                df.to_excel(writer, sheet_name=sheet, index=False)
            print(f"Writing sheet: {sheet}")


def main() -> int:
    _end = date.today()
    _start = _end - timedelta(days=90)
    parser = argparse.ArgumentParser(description="Export KML crop analytics to Excel.")
    parser.add_argument("--local-dir", type=str, default="/opt/prsti/kml_files")
    parser.add_argument("--output", type=str, default="/opt/prsti/kml_output_25.xlsx")
    parser.add_argument("--limit", type=int, default=25, help="Max KML files (default 25)")
    parser.add_argument(
        "--start",
        type=str,
        default=_start.isoformat(),
        metavar="YYYY-MM-DD",
        help=f"Range start (default ~last 90 days: {_start.isoformat()})",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=_end.isoformat(),
        metavar="YYYY-MM-DD",
        help=f"Range end (default today: {_end.isoformat()})",
    )
    parser.add_argument(
        "--recent-days",
        type=int,
        default=None,
        metavar="N",
        help="If set, overrides --start/--end to last N days ending today",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Skip DB; grower_id will be NULL",
    )
    args = parser.parse_args()

    kmls = get_kml_files(args.local_dir, limit=args.limit)
    if not kmls:
        print(f"No KML files found under {args.local_dir}", file=sys.stderr)
        return 1

    if args.recent_days is not None and args.recent_days > 0:
        start_d, end_d = last_n_days_range(args.recent_days)
    else:
        start_d = date.fromisoformat(args.start)
        end_d = date.fromisoformat(args.end)
    if start_d > end_d:
        start_d, end_d = end_d, start_d
    logger.info("Date range: %s → %s (season_id=%s)", start_d, end_d, SEASON_ID)

    db = None
    if not args.no_db:
        try:
            from core.db import SessionLocal
            from sqlalchemy import text

            db = SessionLocal()
            db.execute(text("SELECT 1"))
        except Exception as e:
            logger.warning("DB unavailable (%s); use --no-db or fix connection", e)
            db = None

    dataframes: dict[str, pd.DataFrame] = {}
    total = len(kmls)
    for i, path in enumerate(kmls, start=1):
        print(f"Processing {i}/{total}: {path.name}")
        try:
            df = process_kml(path, start_d, end_d, db)
            lid = None
            if not df.empty and "location_id" in df.columns:
                lid = df["location_id"].iloc[0]
            print(f"Fetched location_id: {lid}")
            dataframes[path.name] = df
        except Exception as e:
            logger.exception("Failed %s: %s", path.name, e)
            dataframes[path.name] = pd.DataFrame({"error": [str(e)]})

    out = Path(args.output)
    write_to_excel(dataframes, out)
    print(f"Wrote {out} ({len(dataframes)} sheets)")
    if db is not None:
        try:
            db.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
