# Field locations + crop_indices alignment (production)

## 1. SQL migration

Run once on the DB (additive columns only):

```bash
psql ... -f sql/migrate_crop_indices_field_locations_alignment.sql
```

Adds: `centroid_lat`, `centroid_lon`, `ndwi_gao`, and band columns `blue` … `swir2` on `operations.crop_indices`. `extracted_grower` is included with `IF NOT EXISTS` if not already present. (Grower text uses `extracted_grower` only; run `sql/alter_crop_indices_drop_extracted_grower_name.sql` if that column was added earlier.)

## 2. Reload strategy

**Step 1 — field_locations (no Google / geocode)**

```bash
python scripts/ingest_field_locations_from_kml_dir.py --local-dir /path/to/kml
```

- Centroid from **polygon** (see KML parser); 7 decimal places.
- `extracted_village` from KML metadata / placemark name.
- Duplicate centroids → skip.

**Step 2 — crop_indices**

```bash
python scripts/run_crop_analysis_s3_batch.py --local-dir /path/to/kml
```

or single-file `run_crop_analysis` with DB. Location rows come **only** from `operations.field_locations` (`get_field_location_row_by_centroid`).

## 3. KML parser

- Prefers **Polygon → outerBoundaryIs → LinearRing → coordinates**.
- Ignores **Point** until polygon pass fails; single Point is expanded to a micro-polygon for API compatibility (logged).
- Logs **Using Polygon coordinates** when polygon ring is used.

## 4. Pipeline / batch behaviour

- **No** `get_location_from_coordinates` (no Nominatim / Google in crop_indices flow).
- Log: **Skipping Google API call (using field_locations only)** (batch) / **Skipping Google API call (crop_indices uses field_locations only)** (pipeline).
- On match: **Location matched from field_locations**.
- Inserts use `district`, `state`, `postcode` from `field_locations` (not geocode).

## 5. Band + index mapping (pipeline snapshot)

| Column        | Source (S2)   |
|---------------|---------------|
| blue … swir2  | B02–B07, B08, B8A, B11, B12 means (NaN if band missing) |
| ndwi_gao      | (NIR − SWIR1) / (NIR + SWIR1) with safe denominator |

Batch time-series: per-day **ndwi_gao** is set from **NDMI** (same SWIR1 formula from Statistical API).

## 6. Idempotency

- `insert_field_location_if_absent`: one row per centroid tolerance.
- Batch: existing `(file_name, analysis_date)` / observation keys still skipped.
