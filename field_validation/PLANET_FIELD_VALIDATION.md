# Field-team CSV enrichment: Sentinel (`crop_indices`) + PlanetScope (PSScene)

This document describes the **standalone** module under `field_validation/` and the runner `scripts/run_field_team_planet_validation.py`. **No changes** are made to `crop_monitoring` ingestion pipelines.

## What was implemented

| Step | Status | Notes |
|------|--------|--------|
| 1 — Filter CSV (SYNC + NDVI satellite) | Done | `field_validation/csv_loader.py`. Typo column **"Sattelite"** is matched explicitly. Current file: **21 rows / 21 unique `location_id`s** (not 23 — data-dependent). |
| 2 — `operations.crop_indices` → `file_name` | Done | `field_validation/db_queries.py`. Optional `season_id` filter. |
| 2 — S3 KML download | Done | `field_validation/s3_kml.py`; tries `basename` under each `--s3-prefix` (default CG/CG path). |
| 3–5 — Planet daily metadata | Done | `field_validation/planet_api.py`: Data API v1 **quick-search** on **PSScene**, geometry + date filter. **One row per calendar day** with `MISSING` / `HAS_SCENE` (no interpolation). |
| 4 — Indices from Planet rasters | Partial | `field_validation/raster_indices.py`: **optional** zonal stats if you place a **local** clipped 4-band analytic GeoTIFF; uses **`crop_monitoring.index_calculator`** (NDVI, SAVI, EVI). **NDMI** needs SWIR → not from standard 4-band analytic. |
| 6 — Excel output | Done | Sheets: `summary`, `location_file_s3_map`, `daily_enriched`. |
| 7 — Exploration | This file | See below. |

## CSV semantics (important)

- **`SYNC (Sattelite)`** in the spreadsheet is a **field phenology / male–female synchronization** style score used in ground validation, **not** a satellite “sync acquisition” flag from imagery providers.
- There is **no** Planet-native “SYNC” equivalent. The export adds `note_csv_sync_is_phenology` and keeps your CSV value in `csv_sync_satellite` for comparison only.

## Planet API: what works vs limits

### What works (with `PLANET_API_KEY`)

- **Quick-search** for **PSScene** items intersecting the **KML polygon** and **date range**.
- Scene-level metadata exposed on items (varies by version): commonly **`cloud_cover`**, **`visible_confidence` / `clear_confidence`**, **`view_angle`**, **`satellite_id`**, **`acquired`**, strip/planet internal ids, etc. The code copies **all non-null** keys it reads from `properties` into flattened `planet_meta_*` columns when a scene exists.
- **Per-pixel cloud / quality**: Planet provides separate **UDM / UDM2** assets (e.g. usable data mask) — **not** downloaded in this script. Use `planet_api.list_item_assets("PSScene", item_id)` and Planet Orders/clipping to retrieve GeoTIFF masks (see Planet docs).
- **“Daily” cadence**: This script emits **one calendar row per day**. **Planet does not guarantee** a cloud-free scene every day; **`MISSING`** means no PSScene item with `acquired` date on that **UTC calendar day** (after search + bucketing).

### What is missing / not automated here

- **Automatic download + clip + zonal statistics** for every scene: requires **Planet Orders API** (or `planet` Python SDK) with **clip** to AOI, async polling, and storage — **not** implemented (would add cost, quota, and runtime).
- **Surface reflectance**: depends on product type (`ortho_analytic_*` vs atmospherically corrected products). Confirm in Planet **asset** list for your contract.
- **Precomputed NDVI from Planet**: not used here; we compute from bands **only** in `raster_indices.py` when a local GeoTIFF is supplied (extend script if you wire downloads).
- **Enterprise Fusion**: explicitly out of scope per your request; only **standard Data API** quick-search path.

### Cost / access

- Planet **commercial** data: API key tied to a Planet account; **download & order** usage typically **consumes quota / charges** per your contract.
- **No API key** → script still runs: Planet columns show `NOT_QUERIED` / `PLANET_API_KEY not set`.

## Sentinel-2 comparison columns

- Join key: **`calendar_date`** ↔ `crop_indices.analysis_date` (same string `YYYY-MM-DD`).
- Exported: `sentinel2_ndvi_db`, `savi`, `ndmi`, `evi`, `file_name` from DB.

## Geometry sources

1. **Preferred for this script:** KML from S3 using `crop_indices.file_name` (same as batch ingestion).
2. **`operations.location_polygons`**: keyed to **village** `location_id` (often `L_*`), **not** field `IND-*` IDs — usually **empty** for these field rows. Not queried in the runner (can be extended).

## PlanetScope vs Sentinel-2 (engineering notes)

| Topic | PlanetScope (typical) | Sentinel-2 L2A |
|--------|------------------------|----------------|
| Spatial resolution | ~3 m (PlanetScope) | 10 m / 20 m bands |
| Revisit | Daily potential, **cloud-limited** | ~5 days + clouds |
| SWIR | Often **8-band** SKUs; standard **4-band analytic** has **no SWIR** | B11/B12 SWIR |
| Scene cloud % | Item metadata + UDM2 | Scene `cloud_cover` + SCL masks |
| Radiometry | Product-dependent (DN vs reflectance) | L2A reflectance |

## How to run

```powershell
cd c:\Users\madan\ENVIRONMENTS\SeedIQ-Prod
python scripts/run_field_team_planet_validation.py `
  --csv "Data Entry_Field Team.csv" `
  --date-start 2024-12-01 `
  --date-end 2025-04-01 `
  --output demo_outputs/field_team_planet_enriched.xlsx
```

Optional:

- `--season-id RABI_25_26` — if `crop_indices.season_id` exists.
- `--skip-planet` — Sentinel + S3 only.
- `--skip-s3` — DB + Planet only (geometry required for Planet; otherwise placeholder rows).
- `--s3-prefix seedworks/kml_files/input_files/KA/KA/` — repeat for multiple regions.

## Additional vegetation metrics (Planet ecosystem)

Beyond NDVI/SAVI/EVI (computed locally): Planet **analytic** products are band data; **Vegetation analytic** or **SR** products (if licensed) may align better with Sentinel-like reflectance. Check Planet’s **item-type & asset** catalog for your subscription. **GLI, EVI, SAVI** can all be computed from reflectance bands with the same formulas as in `crop_monitoring/index_calculator.py` once reflectance scaling is confirmed.

## Files added

- `field_validation/__init__.py`
- `field_validation/csv_loader.py`
- `field_validation/db_queries.py`
- `field_validation/s3_kml.py`
- `field_validation/planet_api.py`
- `field_validation/raster_indices.py`
- `field_validation/output_builder.py`
- `field_validation/PLANET_FIELD_VALIDATION.md` (this file)
- `scripts/run_field_team_planet_validation.py`
