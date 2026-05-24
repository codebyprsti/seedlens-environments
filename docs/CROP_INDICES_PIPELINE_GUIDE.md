# Crop Indices Pipeline — Step-by-Step Guide for the Team

This document explains how we process farm KML files, compute satellite vegetation indices, and store results in the database. Use it to run the pipeline yourself or to onboard new team members.

---

## 1. What This Pipeline Does

1. **Reads** farm boundary polygons from KML files (e.g. from field teams or exports).
2. **Extracts** metadata (village, grower, variety) from the KML filename or placemark name.
3. **Fetches** Sentinel-2 (optical) and Sentinel-3 (thermal) band data from Copernicus for the polygon and date range.
4. **Computes** vegetation and thermal indices (NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, LAI, LST_C).
5. **Resolves** location, grower, and variety IDs from master tables (with fuzzy matching).
6. **Stores** one row per KML in `operations.crop_indices` (indices + raw metadata + geocoded location).

**Output:** One database row per KML with indices, village/grower/variety (raw and resolved IDs), and reverse-geocoded location (district, state, country).

---

## 2. Prerequisites

### 2.1 Environment

- **Python** 3.x with project dependencies installed:
  ```bash
  pip install -r requirements.txt
  ```
  Key packages: `sentinelhub`, `shapely`, `numpy`, `fastkml`, `psycopg2-binary`, `rapidfuzz`, `python-dotenv`, `pydantic-settings`.

- **Project root:** Run all commands from the repository root (e.g. `C:\...\SeedIQ-Prod`).

### 2.2 Configuration (.env)

In the project root, ensure `.env` exists and contains:

- **Database** (for storing results):
  ```
  DB_NAME=dev_seedworks_db
  DB_USER=<your_user>
  DB_PASS=<your_password>
  DB_HOST=<host>
  DB_PORT=5432
  ```

- **Copernicus / Sentinel Hub** (for satellite data):
  ```
  SH_CLIENT_ID=<your_client_id>
  SH_CLIENT_SECRET=<your_client_secret>
  ```
  These are from Copernicus Data Space (or your Sentinel Hub account). Without them, band fetch will fail.

### 2.3 Database Schema

The table `operations.crop_indices` must exist and include all required columns.

**Step 1 — Create table (if not already done):**

Run once:

```bash
psql -h <DB_HOST> -p 5432 -U <DB_USER> -d <DB_NAME> -f sql/create_crop_indices_table.sql
```

**Step 2 — Add missing columns (raw metadata + geocoded location):**

Run once (idempotent):

```bash
psql -h <DB_HOST> -p 5432 -U <DB_USER> -d <DB_NAME> -f sql/alter_crop_indices_add_raw_metadata.sql
```

Or from Python (adds only `grower_name` and `variety_name` if missing):

```bash
python scripts/apply_crop_indices_schema.py
```

**Step 3 — Verify schema:**

```bash
python scripts/check_crop_indices_schema.py
```

You should see: `All required columns exist.`

Required columns include: `id`, `location_id`, `grower_id`, `variety_id`, `grower_name`, `variety_name`, `village`, `town`, `district`, `state`, `country`, `postcode`, `polygon_area`, `date_start`, `date_end`, all index columns (`ndvi` … `lst_celsius`), `created_at`.

---

## 3. Running the Pipeline

### 3.1 Single KML File

To process **one** KML and optionally store in the database:

```bash
# Process and save to DB (default)
python scripts/run_crop_analysis.py "C:\path\to\farm.kml"

# Process without saving to DB (only print report)
python scripts/run_crop_analysis.py "C:\path\to\farm.kml" --no-db

# Custom date range
python scripts/run_crop_analysis.py "C:\path\to\farm.kml" --start 2025-01-01 --end 2025-01-31

# Quieter output (report only, less logging)
python scripts/run_crop_analysis.py "C:\path\to\farm.kml" --quiet
```

**What you see:** KML status (village, grower, variety), master data matching (location/grower/variety IDs and scores), polygon area, bounding box, reverse-geocoded location, and all 10 indices (NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, LAI, LST_C).

### 3.2 Batch — All KML Files in a Folder

To process **every** `.kml` file in a directory and insert each result into `operations.crop_indices`:

```bash
# Default folder (set inside the script)
python scripts/run_crop_analysis_batch.py

# Specific folder (e.g. Chhattisgarh, Telangana, Odisha, Karnataka)
python scripts/run_crop_analysis_batch.py --dir "C:\Users\madan\Downloads\CG-20260302T122713Z-1-001\CG"
python scripts/run_crop_analysis_batch.py --dir "C:\Users\madan\Downloads\Telangana-20260302T142205Z-1-001\Telangana"
python scripts/run_crop_analysis_batch.py --dir "C:\Users\madan\Downloads\Odisha-20260302T142202Z-1-001\Odisha"
python scripts/run_crop_analysis_batch.py --dir "C:\Users\madan\Downloads\Karnataka-20260302T142202Z-1-001\Karnataka"
```

- The script finds all `*.kml` in the given directory.
- For each file it: parses KML → extracts metadata → fetches Sentinel bands → computes indices → resolves IDs → inserts one row and commits.
- If one file fails, the script logs the error and continues with the rest.
- At the end it prints **BATCH PROCESS SUMMARY**: total files, successfully processed, failed, and the list of failed files (if any).

**Do not use `--no-db`** for batch; results are intended to be saved.

---

## 4. Data Flow (Technical Summary)

| Step | What happens |
|------|-------------------------------|
| 1 | **Parse KML** (`crop_monitoring/kml_parser.py`): Read polygon coordinates; normalize to Polygon/MultiPolygon GeoJSON (including when `make_valid` returns a GeometryCollection). |
| 2 | **Reverse geocode** (`crop_monitoring/location_resolver.py`): Centroid of polygon → Nominatim API → village, district, state, country, etc. |
| 3 | **Resolve metadata** (`crop_monitoring/metadata_resolver.py`): From KML name/filename + reverse geocode → village, grower, variety (with optional LLM). |
| 4 | **Fetch bands** (`crop_monitoring/band_extractor.py`, `sentinel_client.py`): One Sentinel-2 request (B02, B03, B04, B05, B08, B11), one Sentinel-3 request (S8, S9) for the polygon and date range. |
| 5 | **Compute indices** (`crop_monitoring/index_calculator.py`, `temperature_calculator.py`): NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, LAI, LST_C (mean over polygon). |
| 6 | **Resolve IDs** (`crop_monitoring/database/repository.py`, `id_resolution.py`): Fuzzy match village → `location_id`, grower → `grower_id`, variety → `variety_id` (thresholds: 85/85/90). |
| 7 | **Insert** (`crop_monitoring/database/repository.py`): One row into `operations.crop_indices` (IDs, indices, raw metadata, geocoded fields); then `commit`. |

---

## 5. What Gets Stored in the Database

Each row in `operations.crop_indices` includes:

- **IDs (can be NULL):** `location_id`, `grower_id`, `variety_id` (from master tables).
- **Raw metadata:** `grower_name`, `variety_name`, `village` (as extracted from KML/filename).
- **Geocoded location:** `village`, `town`, `district`, `state`, `country`, `postcode` (from reverse geocode or KML fallback).
- **Area and dates:** `polygon_area`, `date_start`, `date_end`.
- **Indices:** `ndvi`, `savi`, `ndmi`, `ndre`, `gci`, `psri`, `msavi`, `evi`, `lai`, `lst_celsius`.
- **Audit:** `created_at`.

---

## 6. Validating Results

**Row count:**

```sql
SELECT COUNT(*) FROM operations.crop_indices;
```

**Latest rows (example):**

```sql
SELECT village, grower_name, variety_name, ndvi, lai, lst_celsius, created_at
FROM operations.crop_indices
ORDER BY created_at DESC
LIMIT 10;
```

---

## 7. Troubleshooting

| Issue | What to do |
|-------|------------|
| **"column grower_name (or variety_name) does not exist"** | Run schema updates: `sql/alter_crop_indices_add_raw_metadata.sql` or `python scripts/apply_crop_indices_schema.py`. Then re-run. |
| **"DB not available" / "ImportError: No module named 'psycopg2'"** | Install DB driver: `pip install psycopg2-binary`. Ensure `.env` and DB are reachable. |
| **"Sentinel Hub credentials required"** | Set `SH_CLIENT_ID` and `SH_CLIENT_SECRET` in `.env` (Copernicus Data Space or Sentinel Hub). |
| **"Supported geometry types are polygon and multipolygon, got GeometryCollection"** | Fixed in code: KML parser now converts GeometryCollection to Polygon/MultiPolygon. Update repo and re-run. |
| **UnicodeEncodeError when printing village/grower/variety** | Batch script now uses ASCII-safe printing for console. If you still see errors, ensure the script is up to date. |
| **Some batch files fail** | Check the batch summary for the list of failed files. Inspect logs/traceback for each (e.g. invalid KML, no coordinates, API/network errors). Re-run single-file for that path: `python scripts/run_crop_analysis.py "<path>"`. |

---

## 8. Folders We Processed (Reference)

These are example folders that were run successfully with the batch script:

| Region | Path | Files |
|--------|------|-------|
| Chhattisgarh (CG) | `C:\Users\madan\Downloads\CG-20260302T122713Z-1-001\CG` | 28 |
| Telangana | `C:\Users\madan\Downloads\Telangana-20260302T142205Z-1-001\Telangana` | 26 |
| Odisha | `C:\Users\madan\Downloads\Odisha-20260302T142202Z-1-001\Odisha` | 25 |
| Karnataka | `C:\Users\madan\Downloads\Karnataka-20260302T142202Z-1-001\Karnataka` | 25 |

Command used in each case:

```bash
python scripts/run_crop_analysis_batch.py --dir "<folder_path>"
```

---

## 9. Quick Reference — Commands

```bash
# Schema
python scripts/check_crop_indices_schema.py
python scripts/apply_crop_indices_schema.py

# Single file
python scripts/run_crop_analysis.py "path\to\file.kml" [--no-db] [--quiet]

# Batch
python scripts/run_crop_analysis_batch.py [--dir "path\to\kml\folder"]
```

---

*Last updated to reflect: single and batch pipeline, DB schema (including grower_name/variety_name), fuzzy ID resolution, GeometryCollection handling, and Unicode-safe batch output.*
