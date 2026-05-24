"""
Merge manual CSV fields, crop_indices Sentinel series, and Planet daily calendar rows.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd

from field_validation.date_utils import parse_validation_date_cell
from field_validation.planet_api import planet_data_pipeline_note
from field_validation.planet_sync_metrics import timing_sync_metrics


def _to_calendar_date_key(value: Any) -> Optional[str]:
    """Normalize DB date / datetime / string to YYYY-MM-DD (join key for daily rows)."""
    if value is None:
        return None
    try:
        import pandas as pd

        if isinstance(value, pd.Timestamp):
            return value.date().isoformat()
    except Exception:
        pass
    if isinstance(value, datetime):
        return value.date().isoformat()
    if type(value) is date:
        return value.isoformat()
    s = str(value).strip()
    return s[:10] if len(s) >= 10 else None


def daily_placeholder_without_planet(start: date, end: date, reason: str) -> list[dict[str, Any]]:
    """Explicit calendar rows when Planet API is not used (still no interpolation)."""
    rows: list[dict[str, Any]] = []
    d = start
    while d <= end:
        rows.append(
            {
                "calendar_date": d.isoformat(),
                "planet_status": "NOT_QUERIED",
                "planet_scene_count": 0,
                "planet_best_item_id": None,
                "planet_best_cloud_cover": None,
                "planet_note": reason,
            }
        )
        d += timedelta(days=1)
    return rows


def sentinel_by_date(series: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map calendar date YYYY-MM-DD -> row dict (matches calendar_date in daily rows)."""
    out: dict[str, dict[str, Any]] = {}
    for r in series:
        k = _to_calendar_date_key(r.get("analysis_date"))
        if not k:
            continue
        out[k] = r
    return out


def merge_daily_enrichment(
    *,
    location_id: str,
    csv_static: dict[str, Any],
    file_names: list[str],
    s3_key_used: Optional[str],
    planet_daily_rows: list[dict[str, Any]],
    sentinel_series: list[dict[str, Any]],
    aoi_area_ha: Optional[float] = None,
    default_validation_year: Optional[int] = None,
) -> pd.DataFrame:
    """
    One row per planet_daily_rows entry (calendar grid or observation-only). Joins Sentinel when dates match.
    Planet raw DN/SR are NOT populated unless a future pipeline passes them on each pr dict.
    """
    s_map = sentinel_by_date(sentinel_series)
    vy = default_validation_year or date.today().year
    vd_parsed = parse_validation_date_cell(
        str(csv_static.get("csv_validation_date") or ""),
        default_year=vy,
    )
    flat: list[dict[str, Any]] = []
    for pr in planet_daily_rows:
        cal = pr.get("calendar_date") or ""
        sent = s_map.get(cal, {})
        meta = pr.get("planet_metadata_json") if isinstance(pr.get("planet_metadata_json"), dict) else {}
        row = {
            "location_id": location_id,
            "crop_indices_file_names": ";".join(file_names),
            "s3_kml_key": s3_key_used,
            "aoi_area_ha": aoi_area_ha,
            "geometry_footprint_area_ha": aoi_area_ha,
            "csv_state": csv_static.get("state", ""),
            "csv_field_name_kml": csv_static.get("field_name_kml", ""),
            "csv_sync_satellite": csv_static.get("csv_sync_satellite", ""),
            "csv_ndvi_satellite": csv_static.get("csv_ndvi_satellite", ""),
            "csv_validation_date": csv_static.get("csv_validation_date", ""),
            "csv_sync_source_note": (
                "Manual entry from field form; not from Planet API. "
                "Not a satellite acquisition sync metric unless you redefine it."
            ),
            "note_csv_sync_is_phenology": (
                "Historically: phenology / male-female row sync context in ground form; "
                "not derived from Planet metadata."
            ),
            "calendar_date": cal,
            "acquisition_date": cal,
            "planet_acquisition_datetime_utc": pr.get("planet_acquisition_datetime_utc")
            or pr.get("planet_best_acquired"),
            "sentinel2_ndvi_db": sent.get("ndvi"),
            "sentinel2_savi_db": sent.get("savi"),
            "sentinel2_ndmi_db": sent.get("ndmi"),
            "sentinel2_evi_db": sent.get("evi"),
            "sentinel2_file_name_db": sent.get("file_name"),
            "planet_status": pr.get("planet_status"),
            "planet_scene_count": pr.get("planet_scene_count"),
            "planet_asset_id": pr.get("planet_best_item_id"),
            "planet_best_item_id": pr.get("planet_best_item_id"),
            "cloud_cover": pr.get("planet_best_cloud_cover"),
            "planet_scene_cloud_cover": pr.get("planet_best_cloud_cover"),
            "usable_pixel_percentage": None,
            "planet_clear_confidence": None,
            "planet_best_acquired": pr.get("planet_best_acquired"),
            "planet_note": pr.get("planet_note"),
            "planet_data_pipeline_note": pr.get("planet_pipeline_note") or planet_data_pipeline_note(),
            "planet_processing_level_note": (
                "Data API quick-search does not return L1/L2/SR processing tags. "
                "See planet_available_asset_keys (e.g. ortho_analytic_8b_sr) after Orders delivery."
            ),
            "planet_raw_blue": pr.get("planet_raw_blue"),
            "planet_raw_green": pr.get("planet_raw_green"),
            "planet_raw_red": pr.get("planet_raw_red"),
            "planet_raw_nir": pr.get("planet_raw_nir"),
            "planet_raw_rededge": pr.get("planet_raw_rededge"),
            "planet_raw_swir": pr.get("planet_raw_swir"),
            "planet_raw_bands_note": pr.get(
                "planet_raw_bands_note",
                "Not stored: quick-search returns metadata only. "
                "Band DN/SR require asset activation + GeoTIFF download (Orders API).",
            ),
            "planet_ndvi_raster": pr.get("planet_ndvi_raster") or pr.get("planet_ndvi"),
            "planet_savi_raster": pr.get("planet_savi_raster") or pr.get("planet_savi"),
            "planet_evi_raster": pr.get("planet_evi_raster") or pr.get("planet_evi"),
            "planet_gndvi_raster": pr.get("planet_gndvi_raster") or pr.get("planet_gndvi"),
            "planet_ndre_raster": pr.get("planet_ndre_raster") or pr.get("planet_ndre"),
            "planet_ndmi_raster": pr.get("planet_ndmi_raster") or pr.get("planet_ndmi"),
            "planet_ndmi_explanation": (
                "NDMI needs NIR+SWIR. PlanetScope 4-band analytic has no SWIR; "
                "8-band / SR products may include suitable SWIR — verify asset spec."
            ),
            "planet_available_asset_keys": pr.get("planet_available_asset_keys"),
            "planet_asset_api_note": pr.get("planet_asset_api_note"),
            "planet_radiometric_note": pr.get(
                "planet_radiometric_note",
                "ortho_analytic_* = Planet product types; ortho_analytic_8b_sr implies SR for 8-band — confirm in Orders delivery",
            ),
        }
        ts = timing_sync_metrics(pr, validation_date=vd_parsed)
        row.update(ts)
        row["planet_sync_synchronous_index"] = row.get("planet_timing_sync_index")
        row["planet_sync_lag_days_validation_minus_acq"] = row.get("planet_acq_vs_validation_lag_days")
        if isinstance(meta, dict):
            for k, v in meta.items():
                row[f"planet_meta_{k}"] = v
            row["planet_satellite_id"] = meta.get("satellite_id")
            row["planet_meta_usable_proxy_clear_confidence"] = meta.get("clear_confidence")
            row["planet_meta_usable_proxy_visible_confidence"] = meta.get("visible_confidence")
            cc = meta.get("clear_confidence")
            if cc is not None:
                try:
                    row["planet_clear_confidence"] = float(cc)
                except (TypeError, ValueError):
                    pass
        flat.append(row)
    return pd.DataFrame(flat)


def summary_sheet_rows(
    *,
    filtered_csv: pd.DataFrame,
    location_ids: list[str],
    mapping: list[dict[str, Any]],
) -> pd.DataFrame:
    """location_id, file_name mapping for Excel second sheet."""
    rows = [{"location_id": lid, "note": "expected from manual CSV filter"} for lid in location_ids]
    df_loc = pd.DataFrame(rows)
    df_map = pd.DataFrame(mapping)
    return df_loc, df_map
