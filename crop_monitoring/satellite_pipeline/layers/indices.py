"""
Layer 3: Index calculation — API-first, compute gaps, track missing bands/indices.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from crop_monitoring.sar_calculator import compute_sar_metrics
from crop_monitoring.satellite_pipeline.bands import MANIFESTS, PIPELINE_VERSION
from crop_monitoring.satellite_pipeline.index_registry import (
    compute_s2_extras,
    cross_pol_ratio,
    merge_api_index_sources,
    rvi,
)
from crop_monitoring.temperature_calculator import lst_kelvin


def _missing_bands(row: dict, expected: tuple[str, ...]) -> list[str]:
    missing = []
    for b in expected:
        col = b.lower().replace("b8a", "b8a")
        v = row.get(col) or row.get(f"SENT2_{b}")
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            missing.append(b)
    return missing


def _field_meta(
    *,
    internal_id: Optional[str] = None,
    grower_name: Optional[str] = None,
    grower_id: Optional[str] = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if internal_id:
        out["internal_id"] = internal_id
    if grower_name:
        out["grower_name"] = grower_name
    if grower_id:
        out["grower_id"] = grower_id
    return out


def build_sentinel2_record(
    merged: dict[str, Any],
    *,
    location_id: str,
    file_name: str,
    season_id: Optional[str],
    raw_observation_id: Optional[int],
    max_cloud_cover_pct: float = 60.0,
    internal_id: Optional[str] = None,
    grower_name: Optional[str] = None,
    grower_id: Optional[str] = None,
) -> dict[str, Any]:
    api_sources = merge_api_index_sources(merged)
    extras, miss_calc, comp_sources = compute_s2_extras(merged)
    index_sources = {**api_sources, **comp_sources}

    manifest = MANIFESTS["S2"]
    bands_missing = _missing_bands(merged, manifest.bands)

    rec = {
        "location_id": location_id,
        "file_name": file_name,
        "season_id": season_id,
        "acquisition_date": merged.get("acquisition_date"),
        "raw_observation_id": raw_observation_id,
        "max_cloud_cover_pct": max_cloud_cover_pct,
        "scene_cloud_cover_pct": merged.get("scene_cloud_cover_pct"),
        "valid_pixel_fraction": merged.get("valid_pixel_fraction"),
        "b01": merged.get("b01"),
        "b02": merged.get("b02"),
        "b03": merged.get("b03"),
        "b04": merged.get("b04"),
        "b05": merged.get("b05"),
        "b06": merged.get("b06"),
        "b07": merged.get("b07"),
        "b08": merged.get("b08"),
        "b8a": merged.get("b8a"),
        "b09": merged.get("b09"),
        "b11": merged.get("b11"),
        "b12": merged.get("b12"),
        "ndvi": merged.get("ndvi") or merged.get("NDVI"),
        "savi": merged.get("savi") or merged.get("SAVI"),
        "msavi": merged.get("msavi") or merged.get("MSAVI"),
        "evi": merged.get("evi") or merged.get("EVI"),
        "lai": merged.get("lai") or merged.get("LAI") or extras.get("lai"),
        "gci": merged.get("gci") or merged.get("GCI"),
        "ndre": merged.get("ndre") or merged.get("NDRE"),
        "ndre2": extras.get("ndre2"),
        "cire": extras.get("cire"),
        "mcari": extras.get("mcari"),
        "ndmi": merged.get("ndmi") or merged.get("NDMI"),
        "ndwi": merged.get("ndwi") or merged.get("NDWI"),
        "ndwi_gao": extras.get("ndwi_gao"),
        "mndwi": extras.get("mndwi"),
        "psri": merged.get("psri") or merged.get("PSRI"),
        "gndvi": merged.get("gndvi") or merged.get("GNDVI"),
        "msi": merged.get("msi") or merged.get("MSI"),
        "valid_pixel_percentage": merged.get("valid_pixel_percentage"),
        "cloud_pixel_percentage": merged.get("cloud_pixel_percentage"),
        "shadow_pixel_percentage": merged.get("shadow_pixel_percentage"),
        "masked_pixel_percentage": merged.get("masked_pixel_percentage"),
        "usable_scene": merged.get("usable_scene"),
        "quality_score": merged.get("quality_score"),
        "index_sources": index_sources,
        "bands_missing": bands_missing,
        "indices_missing": miss_calc,
        "pipeline_version": PIPELINE_VERSION,
        **_field_meta(internal_id=internal_id, grower_name=grower_name, grower_id=grower_id),
    }
    ad = rec.get("acquisition_date")
    if ad:
        rec["observation_date"] = ad
    return rec


def build_sentinel1_record(
    vv_mean: Optional[float],
    vh_mean: Optional[float],
    *,
    location_id: str,
    file_name: str,
    season_id: Optional[str],
    acquisition_date: str,
    raw_observation_id: Optional[int] = None,
    internal_id: Optional[str] = None,
    grower_name: Optional[str] = None,
    grower_id: Optional[str] = None,
) -> dict[str, Any]:
    vv_db = vh_db = rat = rv = cr = None
    sources: dict = {"VV": "api", "VH": "api"}
    if vv_mean is not None and vh_mean is not None and vv_mean > 0:
        sar = compute_sar_metrics(
            np.array([vv_mean]),
            np.array([vh_mean]),
        )
        vv_db = float(sar["VV_dB"][0])
        vh_db = float(sar["VH_dB"][0])
        rat = float(sar["vh_vv_ratio"][0])
        sources.update({"VV_dB": "computed", "VH_dB": "computed", "vh_vv_ratio": "computed"})
    rv = rvi(vv_mean, vh_mean)
    cr = cross_pol_ratio(vh_mean, vv_mean)
    if rv is not None:
        sources["rvi"] = "computed"
    if cr is not None:
        sources["cross_pol_ratio"] = "computed"

    bands_missing = []
    if vv_mean is None:
        bands_missing.append("VV")
    if vh_mean is None:
        bands_missing.append("VH")

    return {
        "location_id": location_id,
        "file_name": file_name,
        "season_id": season_id,
        "acquisition_date": acquisition_date,
        "raw_observation_id": raw_observation_id,
        "vv": vv_mean,
        "vh": vh_mean,
        "vv_db": vv_db,
        "vh_db": vh_db,
        "vh_vv_ratio": rat,
        "rvi": rv,
        "cross_pol_ratio": cr,
        "index_sources": sources,
        "bands_missing": bands_missing,
        "pipeline_version": PIPELINE_VERSION,
        "observation_date": acquisition_date,
        **_field_meta(internal_id=internal_id, grower_name=grower_name, grower_id=grower_id),
    }


def build_sentinel3_record(
    s7: Optional[float],
    s8: Optional[float],
    s9: Optional[float],
    *,
    location_id: str,
    file_name: str,
    season_id: Optional[str],
    acquisition_date: str,
    raw_observation_id: Optional[int] = None,
    internal_id: Optional[str] = None,
    grower_name: Optional[str] = None,
    grower_id: Optional[str] = None,
) -> dict[str, Any]:
    lst_k = lst_c = lst_delta = None
    sources: dict = {}
    bands_missing = []
    if s8 is None:
        bands_missing.append("S8")
    if s9 is None:
        bands_missing.append("S9")

    if s8 is not None and s9 is not None:
        lst_k = float(lst_kelvin(np.array([s8]), np.array([s9]))[0])
        lst_c = lst_k - 273.15 if np.isfinite(lst_k) else None
        lst_delta = float(s8 - s9)
        sources = {"S8": "api", "S9": "api", "LST_K": "computed", "LST_C": "computed"}

    return {
        "location_id": location_id,
        "file_name": file_name,
        "season_id": season_id,
        "acquisition_date": acquisition_date,
        "raw_observation_id": raw_observation_id,
        "s7": s7,
        "s8": s8,
        "s9": s9,
        "lst_k": lst_k,
        "lst_celsius": lst_c,
        "lst_delta_k": lst_delta,
        "index_sources": sources,
        "bands_missing": bands_missing,
        "pipeline_version": PIPELINE_VERSION,
        "observation_date": acquisition_date,
        **_field_meta(internal_id=internal_id, grower_name=grower_name, grower_id=grower_id),
    }
