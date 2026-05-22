"""
Crop monitoring pipeline: KML -> bands -> indices -> DB.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any, Optional, Tuple

import numpy as np

from crop_monitoring.kml_parser import parse_kml
from crop_monitoring.band_extractor import fetch_band_data
from crop_monitoring.index_calculator import compute_all_indices
from crop_monitoring.temperature_calculator import lst_celsius
from crop_monitoring.sar_calculator import compute_sar_metrics
from crop_monitoring.database.metadata_parser import extract_metadata
from crop_monitoring.database.repository import (
    get_grower_id,
    get_variety_id,
    insert_crop_indices,
)
from crop_monitoring.database.location_repository import get_field_location_row_by_centroid
from crop_monitoring.metadata_resolver import resolve_metadata
from crop_monitoring.insert_validator import resolve_location_id_from_centroid
from crop_monitoring.cloud_filter import get_primary_maxcc, get_retry_maxcc_sequence
from crop_monitoring.quality_control import validate_indices as qc_validate_indices
from crop_monitoring.metadata_logger import log_run as metadata_log_run

logger = logging.getLogger(__name__)

# Default season for current ingestion pipeline (Rabi 2025–26)
DEFAULT_SEASON_ID = "RABI_25_26"

# Sentinel-2 no-data is typically 0; accept any positive value (use small epsilon for float noise)
_REFLECTANCE_VALID_MIN = 1e-9


def get_rabi_season_date_range() -> Tuple[str, str]:
    """
    Return (start_date, end_date) for current Rabi season: Dec 1 to today.
    If today is before Dec 1, use previous year's Dec 1 as start.
    """
    today = date.today()
    if today.month >= 12:
        start = date(today.year, 12, 1)
    else:
        start = date(today.year - 1, 12, 1)
    return start.isoformat(), today.isoformat()


def _mask_invalid(arr: np.ndarray) -> np.ndarray:
    """Mask non-finite and no-data (<= 0 or < epsilon). Keeps valid reflectance for index calc."""
    return np.where(
        np.isfinite(arr) & (arr >= _REFLECTANCE_VALID_MIN),
        arr,
        np.nan,
    )


def _nanmean_safe(arr: np.ndarray) -> float:
    """Mean ignoring NaN; return float or NaN if all invalid."""
    flat = np.asarray(arr).astype(np.float64).ravel()
    valid = np.isfinite(flat)
    if not np.any(valid):
        return float("nan")
    return float(np.nanmean(flat))


def _has_valid_optical_data(s2_bands: dict) -> bool:
    """True if at least one key optical band has some valid (>0) pixels."""
    if not s2_bands:
        return False
    for b in ("B02", "B04", "B08"):
        arr = s2_bands.get(b)
        if arr is None:
            continue
        flat = np.asarray(arr).ravel()
        if np.any(np.isfinite(flat) & (flat >= _REFLECTANCE_VALID_MIN)):
            return True
    return False


def _polygon_area_ha(geojson: dict) -> float:
    """Polygon area in hectares (WGS84 approximate)."""
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


def _mask_sar_invalid(arr: np.ndarray) -> np.ndarray:
    """Mask SAR backscatter: non-finite or <= 0 -> NaN (for safe log10)."""
    arr = np.asarray(arr, dtype=np.float64)
    return np.where(np.isfinite(arr) & (arr > 0), arr, np.nan)


def _band_mean_safe(s2_bands: dict[str, np.ndarray], key: str) -> float:
    """Mean reflectance for band key if present; else NaN."""
    arr = s2_bands.get(key)
    if arr is None:
        return float("nan")
    return _nanmean_safe(_mask_invalid(arr))


def _compute_means(
    s2_bands: dict[str, np.ndarray],
    s3_bands: dict[str, np.ndarray],
    s1_bands: Optional[dict[str, np.ndarray]] = None,
) -> dict[str, float]:
    """Compute all index arrays then mean per index. Prefer NDVI, SAVI, NDMI from API when present. Add SAR means when s1_bands provided."""
    B02 = _mask_invalid(s2_bands["B02"])
    B03 = _mask_invalid(s2_bands["B03"])
    B04 = _mask_invalid(s2_bands["B04"])
    B05 = _mask_invalid(s2_bands["B05"])
    B08 = _mask_invalid(s2_bands["B08"])
    B11 = _mask_invalid(s2_bands["B11"])

    # Valid-pixel mask for NDVI/SAVI (B08, B04) and for NDMI (B08, B11)
    valid_nr = np.isfinite(B08) & (B08 >= _REFLECTANCE_VALID_MIN) & np.isfinite(B04) & (B04 >= _REFLECTANCE_VALID_MIN)
    valid_ndmi = valid_nr & np.isfinite(B11) & (B11 >= _REFLECTANCE_VALID_MIN)

    # Diagnostic: masked band stats
    for name, arr in [("B02", B02), ("B04", B04), ("B08", B08)]:
        v = np.isfinite(arr) & (arr >= _REFLECTANCE_VALID_MIN)
        n_valid = int(np.sum(v))
        logger.info("[pipeline] after mask %s valid_pixels=%d mean=%s", name, n_valid, float(np.nanmean(arr)) if n_valid else "nan")

    indices = compute_all_indices(B02, B03, B04, B05, B08, B11)
    # Prefer NDVI, SAVI, NDMI from API when present (same formulas, computed in evalscript)
    if "NDVI" in s2_bands and isinstance(s2_bands["NDVI"], np.ndarray):
        arr = np.asarray(s2_bands["NDVI"], dtype=np.float64)
        indices["NDVI"] = np.where(valid_nr, arr, np.nan)
    if "SAVI" in s2_bands and isinstance(s2_bands["SAVI"], np.ndarray):
        arr = np.asarray(s2_bands["SAVI"], dtype=np.float64)
        indices["SAVI"] = np.where(valid_nr, arr, np.nan)
    if "NDMI" in s2_bands and isinstance(s2_bands["NDMI"], np.ndarray):
        arr = np.asarray(s2_bands["NDMI"], dtype=np.float64)
        indices["NDMI"] = np.where(valid_ndmi, arr, np.nan)
    means = {k: _nanmean_safe(v) for k, v in indices.items()}
    logger.info("[pipeline] index_means NDVI=%s SAVI=%s EVI=%s", means.get("NDVI"), means.get("SAVI"), means.get("EVI"))

    # DB / Excel-aligned band means (optional S2 bands)
    means["blue"] = _band_mean_safe(s2_bands, "B02")
    means["green"] = _band_mean_safe(s2_bands, "B03")
    means["red"] = _band_mean_safe(s2_bands, "B04")
    means["rededge1"] = _band_mean_safe(s2_bands, "B05")
    means["rededge2"] = _band_mean_safe(s2_bands, "B06")
    means["rededge3"] = _band_mean_safe(s2_bands, "B07")
    means["nir"] = _band_mean_safe(s2_bands, "B08")
    means["narrow_nir"] = _band_mean_safe(s2_bands, "B8A")
    means["swir1"] = _band_mean_safe(s2_bands, "B11")
    means["swir2"] = _band_mean_safe(s2_bands, "B12")
    # NDWI (Gao) / NDMI (SWIR1): (NIR - SWIR1) / (NIR + SWIR1), safe denominator
    num = B08 - B11
    den = B08 + B11
    valid_gao = (
        valid_ndmi
        & np.isfinite(den)
        & (np.abs(den) >= 1e-10)
    )
    ndwi_gao_arr = np.where(valid_gao, num / den, np.nan)
    means["ndwi_gao"] = _nanmean_safe(ndwi_gao_arr)

    if s3_bands and "S8" in s3_bands and "S9" in s3_bands:
        lst_c = lst_celsius(s3_bands["S8"], s3_bands["S9"])
        means["LST_C"] = _nanmean_safe(lst_c)
    else:
        means["LST_C"] = float("nan")

    # Sentinel-1 SAR: vv_db, vh_db, vh_vv_ratio (mean over polygon; nanmean for safety)
    if s1_bands and "VV" in s1_bands and "VH" in s1_bands:
        vv = _mask_sar_invalid(s1_bands["VV"])
        vh = _mask_sar_invalid(s1_bands["VH"])
        sar_arrays = compute_sar_metrics(vv, vh)
        means["vv_db"] = _nanmean_safe(sar_arrays["VV_dB"])
        means["vh_db"] = _nanmean_safe(sar_arrays["VH_dB"])
        means["vh_vv_ratio"] = _nanmean_safe(sar_arrays["vh_vv_ratio"])
    else:
        means["vv_db"] = float("nan")
        means["vh_db"] = float("nan")
        means["vh_vv_ratio"] = float("nan")

    return means


def run_crop_analysis(
    kml_path: str | Path,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    *,
    store_in_db: bool = True,
    db_session=None,
) -> dict[str, Any]:
    """
    Full pipeline: parse KML, fetch bands, compute indices, optionally store in DB.

    If start_date/end_date are omitted, uses last 30 days.

    Returns:
        {
          "location_id": id or None,
          "grower_id": id or None,
          "variety_id": id or None,
          "indices": { "NDVI": mean, "SAVI": mean, ... "LST_C": mean },
          "metadata": { "village", "grower", "variety" },
        }
    """
    path = Path(kml_path)
    if not path.exists():
        raise FileNotFoundError(f"KML not found: {kml_path}")

    end_default = datetime.now(timezone.utc).date()
    explicit_dates = start_date is not None and end_date is not None
    if start_date is None or end_date is None:
        start_rabi, end_rabi = get_rabi_season_date_range()
        start_date = start_date or start_rabi
        end_date = end_date or end_rabi

    geojson, metadata_kml = parse_kml(path)
    placemark_name = metadata_kml.get("placemark_name") or ""
    filename = path.name

    # Step 2 — Polygon centroid (7 dp). No reverse-geocode / Google API in crop_indices pipeline.
    detected_location: dict[str, Any] = {}
    lat_c, lon_c = None, None
    try:
        from shapely.geometry import shape
        geom = shape(geojson)
        area_ha = _polygon_area_ha(geojson)
        bbox = geom.bounds
        logger.info(
            "[pipeline] polygon CRS=WGS84 area_ha=%.6f bbox=(min_lon=%.6f min_lat=%.6f max_lon=%.6f max_lat=%.6f) valid=%s",
            area_ha, bbox[0], bbox[1], bbox[2], bbox[3], geom.is_valid,
        )
        centroid = geom.centroid
        lon_c = round(float(centroid.x), 7)
        lat_c = round(float(centroid.y), 7)
        logger.info("Processing KML: %s", filename)
        logger.info("Centroid: %s, %s", lat_c, lon_c)
        logger.info("Skipping Google API call (crop_indices uses field_locations only)")
    except Exception as e:
        logger.warning("[pipeline] polygon validation failed: %s; using KML metadata", e)

    # Step 3 — Resolve metadata from KML / filename only (no geocode context)
    resolved = resolve_metadata(
        kml_name=placemark_name,
        filename=filename,
        detected_location=detected_location,
        db_session=None,
        use_llm=True,
    )
    village = resolved.get("village")
    grower = resolved.get("grower_name")
    variety = resolved.get("variety")
    metadata = {
        "village": village,
        "grower": grower,
        "variety": variety,
        "confidence": resolved.get("confidence", {}),
    }

    # When no explicit range: try today first, then last 5 days, then last 30 (least days with latest data)
    s2_bands = s3_bands = None
    s1_bands = {}
    primary_maxcc = get_primary_maxcc()
    logger.info("Fetching indices from: %s → %s", start_date, end_date)
    if not explicit_dates:
        for start_d, end_d in [
            (end_default.isoformat(), end_default.isoformat()),
            ((end_default - timedelta(days=5)).isoformat(), end_default.isoformat()),
            (start_date, end_date),
        ]:
            try:
                s2_bands, s3_bands, s1_bands, _ = fetch_band_data(path, start_d, end_d, maxcc=primary_maxcc)
                if s2_bands and _has_valid_optical_data(s2_bands):
                    start_date, end_date = start_d, end_d
                    break
            except RuntimeError:
                continue
    if s2_bands is None:
        s2_bands, s3_bands, s1_bands, _ = fetch_band_data(path, start_date, end_date, maxcc=primary_maxcc)
    maxcc_used = primary_maxcc
    # Retry with relaxed cloud cover only when no valid data (document: ≤20% primary; fallback from cloud_filter)
    if not _has_valid_optical_data(s2_bands):
        for fallback_maxcc in get_retry_maxcc_sequence():
            logger.warning("[pipeline] S2 optical data empty or all zero; retrying with maxcc=%s", fallback_maxcc)
            try:
                s2_bands, s3_bands, s1_bands, _ = fetch_band_data(path, start_date, end_date, maxcc=fallback_maxcc)
                if _has_valid_optical_data(s2_bands):
                    maxcc_used = fallback_maxcc
                    break
            except RuntimeError:
                pass
    indices_means = _compute_means(s2_bands, s3_bands, s1_bands)
    # Quality control: log warnings if index values outside expected ranges
    qc_ok, qc_warnings = qc_validate_indices(indices_means)
    if not qc_ok:
        for w in qc_warnings:
            logger.warning("[pipeline] QC: %s", w)

    result = {
        "location_id": None,
        "grower_id": None,
        "variety_id": None,
        "indices": indices_means,
        "metadata": metadata,
        "detected_location": detected_location,
        "polygon_area_ha": None,
        "bbox": None,
    }
    try:
        from shapely.geometry import shape
        geom = shape(geojson)
        result["polygon_area_ha"] = _polygon_area_ha(geojson)
        result["bbox"] = geom.bounds
    except Exception:
        pass

    session = db_session
    session_local_cls = None  # set if we create a session; used for close()
    if session is None and store_in_db:
        try:
            from core.db import SessionLocal as session_local_cls
            session = session_local_cls()
        except ImportError:
            pass
        if session is None:
            store_in_db = False

    # Resolve location_id ONLY from operations.field_locations (centroid); grower/variety from master tables
    logger.info(
        "[pipeline] ID resolution input: village=%r, grower=%r, variety=%r",
        village, grower, variety,
    )
    fl_row: Optional[dict[str, Any]] = None
    loc_village_from_field = ""
    loc_id: Optional[str] = None
    if session is not None:
        if lat_c is not None and lon_c is not None:
            fl_row = get_field_location_row_by_centroid(session, lat_c, lon_c)
            if fl_row is not None:
                loc_id = fl_row["location_id"]
                loc_village_from_field = fl_row.get("village") or ""
                result["location_id"] = loc_id
                result["location_matched"] = loc_village_from_field
                result["location_score"] = None
                logger.info(
                    "Centroid match found in operations.field_locations (lat=%s, lon=%s)",
                    lat_c,
                    lon_c,
                )
                logger.info(
                    "Location resolved from field_locations: location_id=%s, village=%s",
                    loc_id,
                    loc_village_from_field or "(empty)",
                )
            else:
                result["location_id"] = None
                result["location_matched"] = None
                result["location_score"] = None
                logger.warning(
                    "[pipeline] No field_locations row for centroid (lat=%s, lon=%s)",
                    lat_c,
                    lon_c,
                )
        else:
            result["location_id"] = None
            result["location_matched"] = None
            result["location_score"] = None

        r_grower = get_grower_id(session, grower, loc_id) if grower else None
        grower_id = r_grower[0] if r_grower else None
        result["grower_id"] = grower_id
        result["grower_matched"] = r_grower[1] if r_grower else None
        result["grower_score"] = r_grower[2] if r_grower else None
        r_var = get_variety_id(session, variety) if variety else None
        var_id = r_var[0] if r_var else None
        result["variety_id"] = var_id
        result["variety_matched"] = r_var[1] if r_var else None
        result["variety_score"] = r_var[2] if r_var else None
        logger.info(
            "[pipeline] ID resolution result: location_id=%s (field_locations only), grower_id=%s, variety_id=%s",
            loc_id,
            grower_id,
            var_id,
        )

    if store_in_db and session is not None:
        # Insert only if centroid matched a field_locations row (already in fl_row)
        if lat_c is None or lon_c is None:
            logger.warning("[pipeline] Missing coordinates; skipping DB insert for %s", filename)
            store_in_db = False
        elif fl_row is None:
            logger.warning(
                "[pipeline] Skipping DB insert: no field_locations match for centroid (%s, %s)",
                lat_c,
                lon_c,
            )
            store_in_db = False
        else:
            logger.info("Season: %s", DEFAULT_SEASON_ID)

        if store_in_db and session is not None:
            # Metadata from KML + DB masters only (no geocode)
            resolved_with_db = resolve_metadata(
                kml_name=placemark_name,
                filename=filename,
                detected_location=detected_location,
                db_session=session,
                use_llm=False,
            )
            village = resolved_with_db.get("village") or village
            grower = resolved_with_db.get("grower_name") or grower
            variety = resolved_with_db.get("variety") or variety
            result["metadata"] = {
                "village": village,
                "grower": grower,
                "variety": variety,
                "confidence": resolved_with_db.get("confidence", metadata.get("confidence", {})),
            }
            logger.info(
                "[pipeline] DB-validated metadata for ID resolution: village=%r, grower=%r, variety=%r",
                village, grower, variety,
            )
            # loc_id and loc_village from centroid lookup (field_locations) above
            loc_id = result["location_id"]
            r_grower = get_grower_id(session, grower, loc_id) if grower else None
            grower_id = r_grower[0] if r_grower else None
            result["grower_id"] = grower_id
            result["grower_matched"] = r_grower[1] if r_grower else None
            result["grower_score"] = r_grower[2] if r_grower else None
            r_var = get_variety_id(session, variety) if variety else None
            var_id = r_var[0] if r_var else None
            result["variety_id"] = var_id
            result["variety_matched"] = r_var[1] if r_var else None
            result["variety_score"] = r_var[2] if r_var else None
            polygon_area = _polygon_area_ha(geojson)
            loc_village = loc_village_from_field  # from field_locations centroid lookup
            loc_town = None
            loc_district = fl_row.get("district") if fl_row else None
            loc_state = fl_row.get("state") if fl_row else None
            loc_country = None
            loc_postcode = fl_row.get("postcode") if fl_row else None
            extracted_village_meta = metadata_kml.get("village_name")
            extracted_grower_meta = metadata_kml.get("extracted_grower")
            resolved_sql_loc = (
                resolve_location_id_from_centroid(session, float(lat_c), float(lon_c))
                if lat_c is not None and lon_c is not None
                else None
            )
            if resolved_sql_loc is None:
                logger.warning(
                    "[pipeline] Skipping DB insert: no field_locations row for ROUND centroid (%s, %s)",
                    lat_c,
                    lon_c,
                )
            else:
                if str(resolved_sql_loc) != str(loc_id):
                    logger.info(
                        "RESOLVED: location_id=%s from centroid lat=%s, lng=%s (was %s)",
                        resolved_sql_loc,
                        lat_c,
                        lon_c,
                        loc_id,
                    )
                loc_id = resolved_sql_loc
                try:
                    row_id = insert_crop_indices(
                        session,
                        {
                            "location_id": loc_id,
                            "season_id": DEFAULT_SEASON_ID,
                            "file_name": filename.replace("+", " ").strip(),
                            "grower_id": grower_id,
                            "variety_id": var_id,
                            "polygon_area": polygon_area,
                            "date_start": start_date,
                            "date_end": end_date,
                            "area_acre": metadata_kml.get("area_acre"),
                            "distance_km": metadata_kml.get("distance_km"),
                            "extracted_village": extracted_village_meta,
                            "extracted_grower": extracted_grower_meta or grower,
                            "centroid_lat": lat_c,
                            "centroid_lon": lon_c,
                            "latitude": lat_c,
                            "longitude": lon_c,
                            "ndvi": indices_means.get("NDVI"),
                            "savi": indices_means.get("SAVI"),
                            "ndmi": indices_means.get("NDMI"),
                            "ndre": indices_means.get("NDRE"),
                            "gci": indices_means.get("GCI"),
                            "psri": indices_means.get("PSRI"),
                            "msavi": indices_means.get("MSAVI"),
                            "evi": indices_means.get("EVI"),
                            "lai": indices_means.get("LAI"),
                            "ndwi_gao": indices_means.get("ndwi_gao"),
                            "lst_celsius": indices_means.get("LST_C"),
                            "vv_db": indices_means.get("vv_db"),
                            "vh_db": indices_means.get("vh_db"),
                            "vh_vv_ratio": indices_means.get("vh_vv_ratio"),
                            "blue": indices_means.get("blue"),
                            "green": indices_means.get("green"),
                            "red": indices_means.get("red"),
                            "rededge1": indices_means.get("rededge1"),
                            "rededge2": indices_means.get("rededge2"),
                            "rededge3": indices_means.get("rededge3"),
                            "nir": indices_means.get("nir"),
                            "narrow_nir": indices_means.get("narrow_nir"),
                            "swir1": indices_means.get("swir1"),
                            "swir2": indices_means.get("swir2"),
                            "grower_name": grower,
                            "variety_name": variety,
                            "village": loc_village,
                            "town": loc_town,
                            "district": loc_district,
                            "state": loc_state,
                            "country": loc_country,
                            "postcode": loc_postcode,
                        },
                    )
                    if row_id <= 0:
                        session.rollback()
                        metadata_log_run(
                            str(path), start_date, end_date, maxcc_used,
                            indices_computed=list(indices_means.keys()),
                            success=False,
                            error="pre-insert validation skipped row",
                        )
                    else:
                        session.commit()
                        metadata_log_run(
                            str(path), start_date, end_date, maxcc_used,
                            indices_computed=list(indices_means.keys()),
                            success=True,
                        )
                except Exception as e:
                    session.rollback()
                    metadata_log_run(
                        str(path), start_date, end_date, maxcc_used,
                        success=False, error=str(e),
                    )
                    raise
            if db_session is None and session_local_cls is not None:
                session.close()

    return result
