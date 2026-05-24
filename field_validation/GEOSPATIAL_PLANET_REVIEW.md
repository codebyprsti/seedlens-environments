# PlanetScope field validation — verified behavior (code + API)

This document answers the six-part geospatial review **from this repository’s implementation**, not from assumed Planet capabilities.

## PART 1 — Band retrieval

| Question | Answer |
|----------|--------|
| Raw Blue/Green/Red/NIR/RedEdge in the enrichment Excel? | **No.** `field_validation/planet_api.py` uses **Data API v1 `quick-search`** only. That returns **scene metadata** (e.g. `id`, `properties.acquired`, `cloud_cover`, `satellite_id`), not pixel values. |
| NDVI from a pre-processed product in this path? | **No NDVI from Planet** in the default script — there is no raster read in `run_field_team_planet_validation.py`. NDVI in the workbook is from **Sentinel-2** (`sentinel2_ndvi_db`) when DB rows exist. Optional **local GeoTIFF** zonal means are in `field_validation/raster_indices.py` (offline file path). |
| Exact asset type requested from Planet API? | **None** in quick-search. Asset **product IDs** (e.g. `ortho_analytic_4b`, `ortho_analytic_8b_sr`, `visual`) appear only if you use **`GET .../items/{id}/assets/`** via `get_asset_keys_for_item` / `--planet-list-assets`. Those keys are **not** analytic_sr vs analytic as a single enum — they are **separate asset names** on the item. |
| Radiometric level (SR vs TOA)? | **Not determined from quick-search.** SR vs TOA is implied by which asset you order (e.g. `*_sr` suffix) after **Orders API** delivery, not from metadata-only export. |
| Actual band names from API? | Quick-search **does not** return band names. **`/assets/`** returns **JSON keys** = asset product IDs, not band names. Band order for GeoTIFF is **product-specific** (see Planet file README); `raster_indices.py` assumes **B,G,R,NIR** for 4-band analytic. |

**If raw bands must be stored:** extend pipeline with **Orders API + download + zonal stats** (or reuse `zonal_mean_bands_4planet_analytic` when a local analytic GeoTIFF exists), then populate `planet_raw_*` columns passed through `merge_daily_enrichment`.

## PART 2 — Indices

| Index | In metadata-only export? | With local analytic GeoTIFF (`raster_indices.py`) |
|-------|--------------------------|---------------------------------------------------|
| NDVI | No | Yes |
| SAVI | No | Yes |
| EVI | No | Yes |
| GNDVI | No | Yes (`planet_gndvi`) |
| NDRE | No | Only if **≥5 bands** and index 4 is RedEdge — **verify band order** for your product |
| NDMI | No | Only if **SWIR** present — **4-band analytic has no SWIR** |

SWIR / NDMI: **not available** on standard 4-band PlanetScope analytic; document any NDMI from 8-band / SR products after confirming band definitions.

## PART 3 — SYNC (`csv_sync_satellite`)

1. **Pulled from Planet API?** **No.**
2. **Derived in code?** **No.**
3. **Source:** **Manual CSV** column mapped as `csv_sync_satellite` in `field_validation/csv_loader.py` / `merge_daily_enrichment`.

**Rule in this codebase:** the field form’s “SYNC (Satellite)” is **not** acquisition timing or automated phenology from satellites. A **satellite-aligned** alternative would be: flag rows where `acquisition_date` (Planet) or Sentinel `analysis_date` is within ±N days of `csv_validation_date`, or expose `planet_acquisition_datetime_utc` vs field visit date.

## PART 4 — Data completeness

`merge_daily_enrichment` now includes (where applicable): `acquisition_date`, `geometry_footprint_area_ha`, `cloud_cover`, `planet_clear_confidence`, `planet_asset_id`, `planet_satellite_id`, `planet_processing_level_note`, `planet_available_asset_keys`, raster index columns (when populated on the row dict), and `planet_data_pipeline_note`.

- **Daily grid (default):** one row per calendar day; missing days show `planet_status=MISSING` (not interpolated).
- **`--planet-observations-only`:** one row per scene only (no gap-filled calendar).

## PART 5 — Accuracy check

Use `scripts/verify_planet_index_math.py --nir ... --red ... [--stored-ndvi ...]`.

Formula: **NDVI = (NIR − Red) / (NIR + Red)** (same as `crop_monitoring.index_calculator.ndvi`).

## PART 6 — Summary

| Topic | Current state |
|-------|----------------|
| Bands fetched (default script) | **Metadata only**, no DN/SR |
| Indices computed (default script) | **S2 from DB**; Planet indices **only if** raster path fills row dicts |
| Missing | Orders API + storage for raw bands per scene |
| SYNC | **CSV manual** — not scientifically derived from Planet |
| API limits | quick-search + `/assets/` do not replace raster delivery |
| Storage | Optional: persist asset IDs + ordered GeoTIFF paths + zonal means per `location_id` × date |

Aligned with Sentinel-2 logic: index formulas live in `crop_monitoring.index_calculator`; Planet zonal path reuses those functions in `field_validation/raster_indices.py` and `planet_indices.py`.
