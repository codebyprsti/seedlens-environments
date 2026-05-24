"""
Zonal polygon mean reflectance + indices — same formulas as crop_monitoring.index_calculator.

Optional: local PlanetScope GeoTIFF (4-band B,G,R,NIR typical). No SWIR => NDMI not computed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from crop_monitoring.index_calculator import evi, ndre, ndvi, savi

from field_validation.planet_indices import gndvi


def zonal_mean_bands_4planet_analytic(
    tif_path: Path,
    geojson_polygon: dict[str, Any],
) -> dict[str, Any]:
    """
    Read 4-band Planet analytic GeoTIFF (assumed order B, G, R, NIR — confirm per product README).

    Returns band means over polygon + NDVI, SAVI, EVI; NDMI None (no SWIR).
    """
    import rasterio
    from rasterio.mask import mask as rio_mask

    with rasterio.open(tif_path) as src:
        arr, _tr = rio_mask(src, [geojson_polygon], crop=True, filled=False)
        if arr.shape[0] < 4:
            raise ValueError(f"Need at least 4 bands for BGRN, got shape {arr.shape}")
        b, g, r, nir = (
            arr[0].astype(np.float64),
            arr[1].astype(np.float64),
            arr[2].astype(np.float64),
            arr[3].astype(np.float64),
        )
        valid = np.isfinite(b) & np.isfinite(g) & np.isfinite(r) & np.isfinite(nir)
        if not np.any(valid):
            return {
                "valid_pixel_count": 0,
                "blue_mean": None,
                "green_mean": None,
                "red_mean": None,
                "nir_mean": None,
                "planet_ndvi": None,
                "planet_savi": None,
                "planet_evi": None,
                "planet_ndmi": None,
                "ndmi_note": "No SWIR in standard 4-band PlanetScope analytic",
            }
        b = np.where(valid, b, np.nan)
        g = np.where(valid, g, np.nan)
        r = np.where(valid, r, np.nan)
        nir = np.where(valid, nir, np.nan)
        ndvi_a = ndvi(nir, r)
        savi_a = savi(nir, r)
        evi_a = evi(nir, r, b)
        gndvi_a = gndvi(nir, g)
        result: dict[str, Any] = {
            "valid_pixel_count": int(np.sum(valid)),
            "blue_mean": float(np.nanmean(b)),
            "green_mean": float(np.nanmean(g)),
            "red_mean": float(np.nanmean(r)),
            "nir_mean": float(np.nanmean(nir)),
            "planet_ndvi": float(np.nanmean(ndvi_a)),
            "planet_savi": float(np.nanmean(savi_a)),
            "planet_evi": float(np.nanmean(evi_a)),
            "planet_gndvi": float(np.nanmean(gndvi_a)),
            "planet_ndre": None,
            "planet_ndmi": None,
            "ndmi_note": "NDMI requires NIR+SWIR; 4-band analytic has no SWIR",
            "radiometric_note": (
                "Band order assumed B,G,R,NIR (ortho_analytic_4b). "
                "Confirm scaling (DN vs reflectance) in Planet file metadata."
            ),
        }
        if arr.shape[0] >= 5:
            # 8-band ordering is product-specific — verify Planet README before trusting NDRE
            re_arr = arr[4].astype(np.float64)
            re_arr = np.where(np.isfinite(re_arr), re_arr, np.nan)
            if np.any(np.isfinite(re_arr)):
                result["planet_ndre"] = float(np.nanmean(ndre(nir, re_arr)))
        # Aliases for merge_daily_enrichment / Excel output
        result["planet_ndvi_raster"] = result.get("planet_ndvi")
        result["planet_savi_raster"] = result.get("planet_savi")
        result["planet_evi_raster"] = result.get("planet_evi")
        result["planet_gndvi_raster"] = result.get("planet_gndvi")
        result["planet_ndre_raster"] = result.get("planet_ndre")
        result["planet_ndmi_raster"] = result.get("planet_ndmi")
        return result
