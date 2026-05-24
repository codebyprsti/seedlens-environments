# Crop Monitoring Pipeline – Summary of Work Done

This document summarizes the crop monitoring analytics pipeline built for processing KML farm polygons, fetching satellite data, computing vegetation indices, and storing results in the database.

---

## 1. Overview

The system takes KML files (farm boundaries from customers), retrieves Sentinel-2 and Sentinel-3 band data from Copernicus, computes vegetation and thermal indices, resolves village/grower/variety to master data IDs, and stores results in `operations.crop_indices`.

---

## 2. What Was Implemented

### 2.1 KML Parsing & Metadata Extraction

- **Module:** `crop_monitoring/kml_parser.py` – parses KML and extracts polygon coordinates (lon, lat; altitude ignored).
- **Metadata:** Placemark names follow patterns like `village-{VillageName} {GrowerName} {VarietyName}` (e.g. `village-Banjari Gumesh kumar sahu Usrh-24`).
- **Parser:** `crop_monitoring/database/metadata_parser.py` – extracts village, grower, variety from placemark name (fixed off-by-one bug: "Banjari" was showing as "anjari"; slice corrected from `s[9:]` to `s[8:]` after `"village-"`).
- **Resolver:** `crop_monitoring/metadata_resolver.py` – handles inconsistent KML names using regex (e.g. variety `USRH24`, `US 26`), optional LLM (SambaNova), and validation against DB; order: KML name → filename → reverse geocode → database matching.

### 2.2 Satellite Band Data & Indices

- **Bands:** Sentinel-2 L2A (B02, B03, B04, B05, B08, B11) for optical indices; Sentinel-3 SLSTR (S8, S9) for land surface temperature.
- **Modules:** `crop_monitoring/sentinel_client.py` (Process API), `crop_monitoring/band_extractor.py`, `crop_monitoring/index_calculator.py`, `crop_monitoring/temperature_calculator.py`.
- **Indices computed:** NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, LAI (from Sentinel-2); LST_C (from Sentinel-3). All use vectorized numpy with masking for invalid/no-data pixels.
- **Debugging (NaN fix):** Relaxed cloud cover (maxcc 50, retry 80), mask threshold (e.g. `>= 1e-9`), `_has_valid_optical_data()` checks, and extended date window (today → last 5 days → last 30 days) so optical indices return valid numbers instead of NaN.

### 2.3 Reverse Geocoding

- **Module:** `crop_monitoring/location_resolver.py` – uses polygon centroid (Shapely) and OpenStreetMap Nominatim API to get village, town, district, state, country, postcode.
- **Integration:** Pipeline calls it after parsing KML; result is used for location fields and as fallback when KML metadata is missing. Rate limiting (e.g. 1 s delay) and a proper User-Agent are applied.

### 2.4 Master Data ID Resolution (Fuzzy Matching)

- **Problem:** KML/filename names often don’t match DB exactly (e.g. JHALJHALIYA vs jhalijhalia, USRH24 vs USRH-24, "Ajay Kumar das" vs "Ajay Kumar Das").
- **Approach:**
  - **Text normalization:** `normalize_text()` – lowercase, strip punctuation/special chars, collapse spaces (`crop_monitoring/database/id_resolution.py`).
  - **Variety standardization:** `normalize_variety()` – regex to canonical form (e.g. USRH-24, US26).
  - **Fuzzy matching:** RapidFuzz – fetch all villages/growers/varieties, normalize, then `extractOne` with thresholds (locations ≥85, growers ≥85, varieties ≥90).
- **Flow:** Try LIKE match first (fast path); if no match, run fuzzy match; log input, matched value, score, and resolved ID. Return `(id, matched_name, score)` for reporting.
- **Modules:** `crop_monitoring/database/id_resolution.py`, `crop_monitoring/database/repository.py`. When no match above threshold, log "No match above threshold. Returning None." and return None (NULLs allowed for IDs in DB).

### 2.5 Database Schema & Storage

- **Table:** `operations.crop_indices`.
- **IDs:** `location_id`, `grower_id`, `variety_id` (FKs to `operations.locations`, `growers`, `varieties`; NULL allowed).
- **Raw metadata (for traceability):** `grower_name`, `variety_name`, `village`, `town`, `district`, `state`, `country`, `postcode`.
- **Indices & geometry:** `polygon_area`, `date_start`, `date_end`, `ndvi`, `savi`, `ndmi`, `ndre`, `gci`, `psri`, `msavi`, `evi`, `lai`, `lst_celsius`, `created_at`.
- **SQL:** `sql/create_crop_indices_table.sql`, `sql/alter_crop_indices_add_geocoded_location.sql`, `sql/alter_crop_indices_add_raw_metadata.sql`. Migration script `scripts/apply_crop_indices_schema.py` adds missing columns (e.g. `grower_name`, `variety_name`) when needed.
- **Insert:** `insert_crop_indices()` in `crop_monitoring/database/repository.py` – parameterized INSERT including all above; NULLs allowed. Log: "Storing crop analysis with village, grower_name and variety_name for debugging and traceability." Commit after each insert in the pipeline.

### 2.6 Pipeline & Scripts

- **Pipeline:** `crop_monitoring/pipeline.py` – `run_crop_analysis(kml_path, start_date, end_date, store_in_db, db_session)`:
  1. Parse KML → polygon/GeoJSON  
  2. Reverse geocode (centroid)  
  3. Resolve metadata (village, grower, variety)  
  4. Fetch Sentinel-2 and Sentinel-3 bands  
  5. Compute indices and polygon means  
  6. Resolve location_id, grower_id, variety_id (with match details and scores)  
  7. If `store_in_db`, optionally re-resolve metadata with DB, then insert and commit  
- **Single-file run:** `scripts/run_crop_analysis.py` – CLI with `--no-db`, `--quiet`, date range; prints KML status, master data matching (input, matched, IDs, scores), MATCH RESULT, and satellite indices.
- **Batch run:** `scripts/run_crop_analysis_batch.py` – scans a directory (default: `C:\Users\madan\Downloads\CG-20260302T122713Z-1-001\CG`) for `*.kml`, runs pipeline for each with DB on, prints per-file summary and final "BATCH PROCESS SUMMARY" (total, success, failed, list of failed files). Continues on failure; full traceback printed on exception.

### 2.7 Configuration & Environment

- **Config:** `core/config.py` – loads `.env` from project root (path fixed so it works regardless of cwd). Includes DB_* and Sentinel Hub `SH_CLIENT_ID`, `SH_CLIENT_SECRET`.
- **DB:** `core/db.py` – SQLAlchemy engine and SessionLocal; credentials from config.

---

## 3. Approaches Used

| Area | Approach |
|------|----------|
| **KML metadata** | Regex + optional LLM; fallback chain: KML name → filename → reverse geocode → DB validation. |
| **Satellite data** | Single Process API request per product (S2, S3); retry with relaxed cloud cover and extended date window if optical data is empty. |
| **Indices** | Vectorized numpy; mask invalid/no-data; polygon mean for reporting. |
| **ID resolution** | Normalize text → LIKE match first → RapidFuzz fuzzy match with configurable thresholds; return (id, matched_name, score) for logging and UI. |
| **Schema evolution** | Idempotent `ADD COLUMN IF NOT EXISTS` migrations; Python helper script to apply missing columns when needed. |
| **Batch robustness** | One DB session for batch; commit per insert in pipeline; catch exceptions per file, log traceback, continue; final summary with failed file list. |
| **Debugging** | Schema check script (`check_crop_indices_schema.py`), full traceback in batch, repository logs (input, matched, score, ID), and "No match above threshold" when fuzzy returns nothing. |

---

## 4. Debugging & Fixes Applied

- **Village "anjari" bug:** Corrected metadata parser slice so "Banjari" is extracted correctly.
- **Sentinel-2 NaNs:** Relaxed cloud filter, retry logic, and date window; validated with `_has_valid_optical_data()`.
- **DB connection:** Load `.env` from project root; try connection + `SELECT 1` on startup; clear message when DB unavailable.
- **Schema mismatch:** Table missing `grower_name`, `variety_name` caused all batch inserts to fail. Added schema check script and apply script; after adding columns, batch completed 28/28.
- **FK / NULLs:** location_id, grower_id, variety_id allow NULL so inserts succeed even when grower (or others) don’t match.

---

## 5. Current Status

- Single KML: end-to-end run and insert work (parse → bands → indices → ID resolution → insert).
- Batch: 28 KML files processed successfully; all rows inserted into `operations.crop_indices`.
- Output: Report includes KML status, master data matching (with scores), MATCH RESULT, indices, and optional reverse-geocode location. Batch prints per-file summary and BATCH PROCESS SUMMARY.

---

## 6. Key Files Reference

| Purpose | Path |
|--------|------|
| Pipeline entry | `crop_monitoring/pipeline.py` |
| KML parse | `crop_monitoring/kml_parser.py` |
| Metadata extraction | `crop_monitoring/database/metadata_parser.py`, `crop_monitoring/metadata_resolver.py` |
| Sentinel client | `crop_monitoring/sentinel_client.py` |
| Indices / temperature | `crop_monitoring/index_calculator.py`, `crop_monitoring/temperature_calculator.py` |
| Reverse geocode | `crop_monitoring/location_resolver.py` |
| ID resolution (fuzzy) | `crop_monitoring/database/id_resolution.py`, `crop_monitoring/database/repository.py` |
| DB insert | `crop_monitoring/database/repository.py` → `insert_crop_indices()` |
| Single run | `scripts/run_crop_analysis.py` |
| Batch run | `scripts/run_crop_analysis_batch.py` |
| Schema check / apply | `scripts/check_crop_indices_schema.py`, `scripts/apply_crop_indices_schema.py` |
| SQL migrations | `sql/create_crop_indices_table.sql`, `sql/alter_crop_indices_add_geocoded_location.sql`, `sql/alter_crop_indices_add_raw_metadata.sql` |

---

*Document generated as a summary of the crop monitoring pipeline implementation and approaches.*
