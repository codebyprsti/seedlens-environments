# Crop indices extraction — end-to-end steps

This document describes how field polygons (KML) become rows in `operations.crop_indices`, including satellite retrieval, index definitions, and database writes. It reflects the code paths under `crop_monitoring/` and `scripts/run_crop_analysis_s3_batch.py`.

---

## 1. Overview

There are **two** ways indices reach the database:

| Path | Entry point | How indices are produced |
|------|-------------|---------------------------|
| **A. Batch (Statistical API)** | `scripts/run_crop_analysis_s3_batch.py` (default `crop_indices` mode) | Sentinel Hub **Statistical API**: one request per KML polygon over a date range; **daily** mean per index. LST and SAR come from separate **Process API** calls. |
| **B. Pixel pipeline** | `crop_monitoring.pipeline.run_crop_analysis()` | Process API fetches **rasters** (S2 bands); `index_calculator.compute_all_indices()` derives indices; polygon means are aggregated in Python. |

Production batch runs today use **path A** unless you call the pipeline directly.

---

## 2. Prerequisites

1. **Copernicus Data Space / Sentinel Hub OAuth**  
   Set `SH_CLIENT_ID` and `SH_CLIENT_SECRET` (see `core.config.settings` or environment). The client uses CDSE endpoints (`crop_monitoring.sentinel_client`: `sh.dataspace.copernicus.eu`).

2. **`operations.field_locations`**  
   For batch mode, `location_id` and admin fields (village, district, state, mandal, postcode) are resolved **only** by matching the polygon **centroid** (lat/lon rounded to 7 decimals) to a row in `operations.field_locations`. If there is no row, the file is skipped (no crop_indices insert).

3. **Optional backfill before batch**  
   `run_crop_analysis_s3_batch.py --mode locations_only` parses KML, computes centroid, skips if that centroid already exists (same 7 dp rounding), otherwise calls **Google Geocoding API** (`GOOGLE_API_KEY` or `GOOGLE_MAPS_API_KEY`) and inserts via `get_field_location_id()`.

4. **Master data for grower / variety**  
   `resolve_metadata()` derives village, grower name, and variety from placemark name / filename (and optional LLM). `get_grower_id` / `get_variety_id` map names to IDs for the insert.

---

## 3. Path A — S3/local batch (`crop_indices` mode)

### Step 1 — Discover KMLs

- **S3:** List keys under `--prefix` in bucket `prsti-public-data` (credentials from `MyLambdaCredentials` in Secrets Manager when not using local AWS profile).
- **Local:** Recursive `*.kml` under `--local-dir`.

### Step 2 — Resumability check

- If `(file_name, season_id)` is already recorded for the season (default `RABI_25_26`), the file is skipped (`crop_indices_file_processed_for_season`).

### Step 3 — Download and parse KML

- `crop_monitoring.kml_parser.parse_kml()`:
  - Prefers **polygon** geometry (`outerBoundaryIs` / `LinearRing`) over points.
  - Handles `GeometryCollection` / `MultiPolygon` by taking the **largest** polygon by area.
  - Strips **Z** (altitude): 2D geometry for GeoJSON.
  - Returns GeoJSON polygon plus metadata (placemark name, optional `area_acre`, `distance_km`, extracted village/grower when present).

### Step 4 — Centroid

- Shapely centroid of the polygon; **lon** and **lat** rounded to **7** decimal places (aligned with `field_locations` lookup).

### Step 5 — Metadata resolution (no location table from `operations.locations`)

- `resolve_metadata(..., db_session=db, use_llm=False)` in batch: extracts **grower / variety / village hints** from KML name and filename.  
- **Location row:** `get_field_location_row_by_centroid(db, lat, lon)` — SQL match on `ROUND(latitude,7)` and `ROUND(longitude,7)`. If `None`, processing stops for that file.

### Step 6 — IDs and polygon area

- `get_grower_id`, `get_variety_id` (and crop name/id when available from variety resolution).
- Polygon area (hectares): WGS84 → Web Mercator via `pyproj`, area / 10 000.

### Step 7 — Parallel satellite requests (per file)

Three calls run concurrently (`ThreadPoolExecutor`):

1. **S2 indices time series — Statistical API**  
   - `crop_monitoring.statistical_client.fetch_s2_indices_timeseries(geojson, start, end, maxcc=20)`.  
   - Collection: **Sentinel-2 L2A**.  
   - Aggregation: **`P1D`** (one interval per day).  
   - Evalscript inputs: `B02, B03, B04, B05, B08, B11` + `dataMask`.  
   - Outputs (daily means): NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, **NDWI** (McFeeters-style from green and NIR in script), then **LAI** derived in Python as `LAI = 3.618 * EVI - 0.118` (same as `lai_from_evi` in `index_calculator.py`).

2. **LST — Sentinel-3 SLSTR (Process API)**  
   - `fetch_s3_thermal(geojson, (end−10 days, end))` — S8/S9 brightness temperature.  
   - `temperature_calculator.lst_celsius`: Kelvin from T8/T9 with emissivity 0.97, then −273.15 for °C.  
   - Batch stores **one scalar per file**: mean LST over valid pixels for that snapshot (not per Statistical interval).

3. **SAR — Sentinel-1 GRD (Process API)**  
   - `fetch_s1_sar(geojson, (start, end))` — VV, VH.  
   - `sar_calculator.compute_sar_metrics`: `VV_dB`, `VH_dB`, `VH/VV`; batch stores **polygon means** per file.

If the Statistical API returns **no** daily rows, the file is skipped (no insert). LST/SAR failures are logged but do not always abort the file.

### Step 8 — Per-day database rows

For each Statistical API interval:

- Skip if duplicate `(file_name, analysis_date)` or duplicate `(location_id, date_start, variety_id, file_name)` per repository rules.
- `insert_crop_indices()` writes one row with:
  - Identity: `file_name`, `analysis_date`, `season_id`, `location_id`, `grower_id`, `variety_id`, `crop_id`/`crop_name` when resolved.
  - Geometry/context: `polygon_area`, `date_start`/`date_end`, `centroid_lat`/`centroid_lon`, optional `area_acre`, `distance_km`, extracted village/grower, resolved village/district/state/postcode from `field_locations`.
  - **Indices from Statistical API:** NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, LAI; **NDWI** column from API NDWI; **`ndwi_gao`** in batch is set equal to **NDMI** for that day (comment in code: same as NDMI when from SWIR1).
  - **LST / SAR:** same file-level means repeated on each day row for that file (`lst_celsius`, `vv_db`, `vh_db`, `vh_vv_ratio`).

### Step 9 — Commit

- One `commit()` per file after all inserts for that file.

---

## 4. Path B — `run_crop_analysis()` (band rasters)

High-level sequence:

1. Parse KML (same parser).
2. Optionally fetch S2 rasters via `band_extractor.fetch_band_data` (Process API, evalscript with bands + optional NDVI/SAVI/NDMI channels).
3. Mask invalid reflectance (non-finite, ≤ 0).
4. `compute_all_indices(B02…B11)` → NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, LAI (from EVI).
5. Optionally overwrite NDVI/SAVI/NDMI from API layers when present (same formulas).
6. NDWI (Gao): \((\mathrm{NIR}-\mathrm{SWIR1})/(\mathrm{NIR}+\mathrm{SWIR1})\) with safe denominator — **polygon mean** stored as `ndwi_gao`.
7. S3 LST and S1 SAR as in path A (means over polygon).
8. QC (`quality_control.validate_indices`) and optional DB insert via `insert_crop_indices`.

Use this path when you need **per-band rasters** or stricter cloud handling (`cloud_filter` / retry maxcc), not the single Statistical request.

---

## 5. Index formulas (Statistical evalscript alignment)

Implemented in `STATISTICAL_INDICES_EVALSCRIPT` (`statistical_client.py`); Python `index_calculator.py` mirrors the optical indices for path B.

| Index | Formula (conceptual) |
|--------|----------------------|
| **NDVI** | \((\mathrm{B08}-\mathrm{B04})/(\mathrm{B08}+\mathrm{B04})\) |
| **SAVI** | \(((\mathrm{B08}-\mathrm{B04})/(\mathrm{B08}+\mathrm{B04}+0.5))\times 1.5\) |
| **NDMI** | \((\mathrm{B08}-\mathrm{B11})/(\mathrm{B08}+\mathrm{B11})\) |
| **NDRE** | \((\mathrm{B08}-\mathrm{B05})/(\mathrm{B08}+\mathrm{B05})\) |
| **GCI** | \(\mathrm{B08}/\mathrm{B03} - 1\) |
| **PSRI** | \((\mathrm{B04}-\mathrm{B03})/\mathrm{B08}\) |
| **MSAVI** | Modified soil-adjusted VI from B08/B04 (discriminant form in code) |
| **EVI** | \(2.5(\mathrm{B08}-\mathrm{B04})/(\mathrm{B08}+6\mathrm{B04}-7.5\mathrm{B02}+1)\) |
| **NDWI** (in Statistical script) | McFeeters: \((\mathrm{B03}-\mathrm{B08})/(\mathrm{B03}+\mathrm{B08}+\epsilon)\), clamped to \([-1,1]\) |
| **LAI** | \(3.618\times\mathrm{EVI} - 0.118\) (post-processing on API EVI mean) |

**LST:** `lst_kelvin` then Celsius — see `temperature_calculator.py` (T8/T9, emissivity 0.97).

**SAR:** \(10\log_{10}(\mathrm{VV})\), \(10\log_{10}(\mathrm{VH})\), \(\mathrm{VH}/\mathrm{VV}\) — see `sar_calculator.py`.

---

## 6. Key files (quick reference)

| Concern | Module / script |
|--------|------------------|
| KML → GeoJSON + metadata | `crop_monitoring/kml_parser.py` |
| Statistical API time series | `crop_monitoring/statistical_client.py` |
| Process API S2/S3/S1 | `crop_monitoring/sentinel_client.py` |
| Indices from bands (path B) | `crop_monitoring/index_calculator.py` |
| LST | `crop_monitoring/temperature_calculator.py` |
| SAR | `crop_monitoring/sar_calculator.py` |
| Grower/variety/village text parsing | `crop_monitoring/metadata_resolver.py` |
| `field_locations` lookup/insert | `crop_monitoring/database/location_repository.py` |
| `crop_indices` INSERT | `crop_monitoring/database/repository.py` (`insert_crop_indices`) |
| S3/local batch orchestration | `scripts/run_crop_analysis_s3_batch.py` |
| Full pixel pipeline | `crop_monitoring/pipeline.py` |

---

## 7. Related reading

- [SATELLITE_DATA_AND_INDICES_GUIDE.md](./SATELLITE_DATA_AND_INDICES_GUIDE.md) — satellite context and indices overview  
- [INDICES_AND_BANDS_REFERENCE.md](./INDICES_AND_BANDS_REFERENCE.md) — band and index reference  

---

## 8. Command examples

```bash
# Batch: default Rabi window, S3 prefix
python scripts/run_crop_analysis_s3_batch.py --prefix "seedworks/kml_files/input_files/Odisha/BHADRAK/"

# Backfill field_locations only (Google + DB), then run indices separately
python scripts/run_crop_analysis_s3_batch.py --prefix "..." --mode locations_only
python scripts/run_crop_analysis_s3_batch.py --prefix "..."

# Optional log file (structured logging handler)
python scripts/run_crop_analysis_s3_batch.py --prefix "..." --log-file logs/crop_batch.log
```

Date overrides: `--start YYYY-MM-DD`, `--end YYYY-MM-DD`.
