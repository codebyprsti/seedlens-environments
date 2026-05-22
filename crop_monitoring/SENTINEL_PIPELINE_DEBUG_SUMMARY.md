# Sentinel Pipeline Debug – Root Cause and Fixes

## 1. Root Cause Analysis

**Why vegetation indices were NaN**

- **Primary cause:** The first Sentinel-2 request (today-only or strict date window) often returned **no valid optical data**: all band values were 0 (no clear pixels in that time slice).
- **Contributing factor:** `maxCloudCoverage` was set to **20%**. For small farm polygons and short date ranges, the only available L2A scene often had >20% cloud, so it was filtered out and the API returned zeros.
- **Effect:** Pipeline correctly treated 0 as no-data and masked it. With no valid pixels, index means became NaN. LST_C worked because Sentinel-3 SLSTR is requested separately with no cloud filter and had valid thermal data.

**Data flow verified**

1. **KML** → `parse_kml()` → valid GeoJSON polygon (CRS WGS84).
2. **Polygon** → `fetch_band_data()` → one S2 request (evalscript B02,B03,B04,B05,B08,B11), one S3 request (S8,S9).
3. **S2 response** → raw band arrays; originally all zeros for the first try.
4. **Mask** → `_mask_invalid()` keeps reflectance ≥ 1e-9; zeros → NaN.
5. **Indices** → `compute_all_indices()` → NDVI, SAVI, … LAI; formulas and band mapping are correct.
6. **Means** → `_nanmean_safe()`; if all NaN → index mean NaN.

## 2. Fixes Applied

| Change | File | Purpose |
|--------|------|---------|
| Default **maxcc 50** | `sentinel_client.py`, `band_extractor.py` | Allow more cloudy scenes so optical data is returned. |
| **Retry with maxcc=80** | `pipeline.py` | If S2 bands are all zero/nan, retry once with relaxed cloud cover. |
| **Polygon logging** | `pipeline.py` | Log area_ha, bbox, CRS, validity after parse. |
| **S2 band diagnostics** | `sentinel_client.py` | Log per-band pixels, valid_gt0, mean, min, max after API response. |
| **Mask relaxation** | `pipeline.py` | Use `arr >= 1e-9` instead of `arr > 0` to avoid dropping valid low reflectance. |
| **`_has_valid_optical_data()`** | `pipeline.py` | Detect when S2 response has no usable optical pixels to trigger retry. |
| **Index/pipeline logs** | `pipeline.py` | Log valid_pixels and index means (NDVI, SAVI, EVI) after compute. |
| **Script logging** | `run_crop_analysis.py` | Enable INFO logging and print index summary / NaN count. |

## 3. Evalscript (unchanged; already correct)

```javascript
//VERSION=3
function setup() {
  return {
    input: ["B02", "B03", "B04", "B05", "B08", "B11"],
    output: { bands: 6, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [sample.B02, sample.B03, sample.B04, sample.B05, sample.B08, sample.B11];
}
```

Band mapping in Python: B02→0, B03→1, B04→2, B05→3, B08→4, B11→5. No change.

## 4. Index Formulas (verified)

- NDVI = (B08 − B04) / (B08 + B04)
- SAVI = ((B08 − B04) / (B08 + B04 + 0.5)) × 1.5
- NDMI = (B08 − B11) / (B08 + B11)
- NDRE = (B08 − B05) / (B08 + B05)
- GCI = (B08 / B03) − 1
- PSRI = (B04 − B03) / B08
- MSAVI = (2×B08 + 1 − sqrt((2×B08+1)² − 8×(B08−B04))) / 2
- EVI = 2.5 × (B08 − B04) / (B08 + 6×B04 − 7.5×B02 + 1)
- LAI = 3.618 × EVI − 0.118

All implemented with safe division (denominator 0 → NaN). No formula changes.

## 5. Example Debug Logs (after fix)

```
[pipeline] polygon CRS=WGS84 area_ha=0.452351 bbox=(min_lon=81.607532 ...) valid=True
[S2 bands] B02 shape=(64, 64) pixels=4096 valid_gt0=0 mean=0.000000 ...
[S2 bands] ... (first try: all zeros)
[S2 bands] B02 shape=(64, 64) pixels=4096 valid_gt0=2548 mean=0.022075 ...
[pipeline] after mask B02 valid_pixels=2548 mean=0.035...
[pipeline] index_means NDVI=0.850... SAVI=0.568... EVI=0.665...
Indices (means): NDVI: 0.85, SAVI: 0.57, ... LST_C: 32.48
(all indices have valid numeric values)
```

## 6. SQL Schema (indices table)

**Current table used by code:** `sql/create_crop_indices_table.sql`

- `location_id`, `grower_id`, `variety_id` (from operations.locations, growers, varieties)
- `polygon_area`, `date_start`, `date_end`
- `ndvi`, `savi`, `ndmi`, `ndre`, `gci`, `psri`, `msavi`, `evi`, `lai`, `lst_celsius`
- `created_at`

**Extended schema (optional):** `sql/crop_indices_schema.sql`

- Adds `polygon_id`, `analysis_date`, renames `lst_celsius` → `lst_c` if desired. Use as reference or migration.

## 7. Confirmation

After the fixes, a run on the Banjari KML with default (no) date range and `--no-db` produced:

- **NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, LAI, LST_C** all as valid numbers.
- First S2 request: all zeros (no optical data); retry with relaxed cloud / date window returned 2548 valid pixels and valid indices.

No changes were made to the index formulas or band mapping; the issue was entirely due to the first request returning no optical data and was fixed by relaxing cloud cover and retrying.
