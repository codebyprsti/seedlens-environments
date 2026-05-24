# Crop Indices Pipeline: Extraction and Formulas

This document describes what the SeedIQ crop monitoring pipeline does, how indices are extracted from satellite data, and the **exact formulas** used for all vegetation/optical indices, NDWI, SAR metrics, and land surface temperature.

---

## Formulas Quick Reference (all indices used in the pipeline)

| Index | Formula | Bands / inputs |
|-------|---------|----------------|
| **NDVI** | (NIR − Red) / (NIR + Red) | B08, B04 |
| **NDWI** | (Green − NIR) / (Green + NIR) | B03, B08 |
| **SAVI** | 1.5 × (NIR − Red) / (NIR + Red + 0.5) | B08, B04, L = 0.5 |
| **NDMI** | (NIR − SWIR) / (NIR + SWIR) | B08, B11 |
| **NDRE** | (NIR − RedEdge) / (NIR + RedEdge) | B08, B05 |
| **GCI** | (NIR / Green) − 1 | B08, B03 |
| **PSRI** | (Red − Green) / NIR | B04, B03, B08 |
| **MSAVI** | (2·NIR + 1 − √[(2·NIR+1)² − 8(NIR−Red)]) / 2 | B08, B04 |
| **EVI** | 2.5 × (NIR − Red) / (NIR + 6·Red − 7.5·Blue + 1) | B08, B04, B02 |
| **LAI** | 3.618 × EVI − 0.118 | From EVI |
| **VV_dB** | 10 × log₁₀(VV) | S1 VV (linear) |
| **VH_dB** | 10 × log₁₀(VH) | S1 VH (linear) |
| **vh_vv_ratio** | VH / VV | S1 VV, VH (linear) |
| **LST (°C)** | LST_K − 273.15 | LST_K from S3 S8, S9 (see §6) |

---

## 1. What We Have Done (Pipeline Overview)

### 1.1 End-to-end flow

1. **Input:** Farm boundary polygons from KML files (local or S3).
2. **Geometry:** Parse KML → GeoJSON polygon; compute **centroid** (lat, lon) rounded to 7 decimal places.
3. **Location resolution:** Look up **location_id** and **village** from `operations.field_locations` using the centroid with a ~1 m tolerance (`ABS(latitude - lat) < 0.00001`, `ABS(longitude - lon) < 0.00001`). If no match, the record is skipped (no insert).
4. **Date range:** For the current ingestion pipeline we use the **Rabi season** window: start = **1 December** (current year; if today is before 1 Dec, previous year’s 1 Dec), end = **today**.
5. **Satellite data:** Fetch Sentinel-2 (optical), Sentinel-3 (thermal), and Sentinel-1 (SAR) data for the polygon and date range. No changes were made to `sentinel_client.py`, `index_calculator.py`, or `temperature_calculator.py` for formula logic.
6. **Indices:** Compute vegetation indices from S2 bands (and optionally use NDVI, SAVI, NDMI from the API when returned). Compute SAR metrics from S1 VV/VH. Compute LST from S3 S8/S9.
7. **Aggregation:** For each product we take the **mean over the polygon** (NaN-safe) and store one value per index per observation.
8. **Storage:** One row per observation in `operations.crop_indices` with **location_id**, **village** (from field_locations), **season_id** (e.g. `RABI_25_26`), **date_start** / **date_end**, all index columns, **area_acre**, **distance_km**, **file_name**, and metadata.

### 1.2 Season and location

- **season_id:** Identifies the crop season (e.g. `RABI_25_26`). Added to `operations.crop_indices` via `sql/alter_crop_indices_add_season_id.sql`.
- **location_id:** Comes from **centroid-based lookup** in `operations.field_locations` (not from village name matching). Together with **season_id** this uniquely identifies field observations per season.
- **village:** Taken from the matched `field_locations` row when inserting.

### 1.3 Error handling

- Missing centroid → log warning, skip DB insert.
- No matching row in `field_locations` for the centroid → log warning, skip record.
- Invalid/missing band data → indices produce NaN; pipeline continues and can store NaN where allowed.

---

## 2. Data Sources (Bands)

| Source | Bands / products | Description |
|--------|------------------|-------------|
| **Sentinel-2 L2A** | B02, B03, B04, B05, B08, B11 | Reflectance (0–1). B02=Blue, B03=Green, B04=Red, B05=Red Edge, B08=NIR, B11=SWIR. |
| **Sentinel-2 L2A** (optional from API) | NDVI, SAVI, NDMI | Same formulas as below; computed in evalscript when requested. |
| **Sentinel-3 SLSTR** | S8, S9 | Brightness temperature (Kelvin) for LST. |
| **Sentinel-1** | VV, VH | Backscatter coefficient (linear) for SAR. |

All band data are fetched for the **polygon** and **date range**; indices are then computed per pixel and aggregated (mean) over the polygon.

---

## 3. How We Extract the Indices

1. **Load geometry:** KML → GeoJSON polygon (and centroid for location lookup).
2. **Request bands:** One or more Process API (or Statistical API) requests for the polygon and time window return:
   - S2: B02, B03, B04, B05, B08, B11 (and optionally NDVI, SAVI, NDMI from evalscript).
   - S3: S8, S9 (Kelvin).
   - S1: VV, VH (linear backscatter).
3. **Mask invalid values:** For optical bands we mask non-finite and reflectance ≤ 0 (or &lt; small ε). For SAR we mask non-finite and ≤ 0 before log or ratio.
4. **Compute indices:** Apply the formulas below per pixel (vectorized NumPy).
5. **Aggregate:** Take the **mean** over all valid pixels in the polygon for each index (NaN ignored). That single value per index is what we store.
6. **Store:** Insert one row into `operations.crop_indices` with those means plus location_id, village, season_id, dates, area_acre, distance_km, file_name, etc.

So “extraction” = **bands → formulas (per pixel) → polygon mean → one value per index per observation**.

---

## 4. Vegetation and Optical Indices (Formulas)

Band mapping: **B02** = Blue, **B03** = Green, **B04** = Red, **B05** = Red Edge, **B08** = NIR, **B11** = SWIR. All formulas use reflectance (0–1). Division by zero or invalid inputs produce NaN; results are clipped where noted.

| Index | Formula | Bands | Notes |
|-------|---------|--------|--------|
| **NDVI** | \( \displaystyle \frac{\text{NIR} - \text{Red}}{\text{NIR} + \text{Red}} \) | B08, B04 | Clipped to [-1, 1]. |
| **NDWI** | \( \displaystyle \frac{\text{Green} - \text{NIR}}{\text{Green} + \text{NIR}} \) | B03, B08 | Water/moisture index; clipped to [-1, 1]. In evalscript denominator uses +1e-8 to avoid division by zero. |
| **SAVI** | \( \displaystyle (1 + L)\,\frac{\text{NIR} - \text{Red}}{\text{NIR} + \text{Red} + L},\quad L = 0.5 \) | B08, B04 | So SAVI = 1.5 × (NIR−Red)/(NIR+Red+0.5). |
| **NDMI** | \( \displaystyle \frac{\text{NIR} - \text{SWIR}}{\text{NIR} + \text{SWIR}} \) | B08, B11 | Clipped to [-1, 1]. |
| **NDRE** | \( \displaystyle \frac{\text{NIR} - \text{RedEdge}}{\text{NIR} + \text{RedEdge}} \) | B08, B05 | Clipped to [-1, 1]. |
| **GCI** | \( \displaystyle \frac{\text{NIR}}{\text{Green}} - 1 \) | B08, B03 | Green = 0 → NaN. |
| **PSRI** | \( \displaystyle \frac{\text{Red} - \text{Green}}{\text{NIR}} \) | B04, B03, B08 | |
| **MSAVI** | \( \displaystyle \frac{2\,\text{NIR} + 1 - \sqrt{(2\,\text{NIR}+1)^2 - 8(\text{NIR}-\text{Red})}}{2} \) | B08, B04 | Discriminant &lt; 0 → NaN; result clipped to [-1, 1]. |
| **EVI** | \( \displaystyle G\,\frac{\text{NIR} - \text{Red}}{\text{NIR} + C_1\,\text{Red} - C_2\,\text{Blue} + 1} \) | B08, B04, B02 | Default: G=2.5, C₁=6, C₂=7.5. |
| **LAI** | \( \displaystyle a\,\text{EVI} + b \) | From EVI | Default: a = 3.618, b = −0.118 → LAI = 3.618×EVI − 0.118. |

**Code reference:** `crop_monitoring/index_calculator.py` (e.g. `ndvi`, `savi`, `ndmi`, `ndre`, `gci`, `psri`, `msavi`, `evi`, `lai_from_evi`, `compute_all_indices`). NDWI is computed in the Statistical API evalscript only (`crop_monitoring/statistical_client.py`); `index_calculator.py` does not define `ndwi` but the same formula applies.

---

## 5. SAR Formulas (Sentinel-1)

We use **VV** and **VH** backscatter (linear, not dB) from Sentinel-1. Invalid or non-positive values are masked (result NaN).

### 5.1 Backscatter in dB

| Metric | Formula | Description |
|--------|---------|-------------|
| **VV_dB** | \( \displaystyle \sigma^0_{\text{VV,dB}} = 10\,\log_{10}(\text{VV}) \) | VV in dB. VV ≤ 0 or non-finite → NaN. |
| **VH_dB** | \( \displaystyle \sigma^0_{\text{VH,dB}} = 10\,\log_{10}(\text{VH}) \) | VH in dB. VH ≤ 0 or non-finite → NaN. |

### 5.2 VH/VV ratio (linear)

| Metric | Formula | Description |
|--------|---------|-------------|
| **vh_vv_ratio** | \( \displaystyle \frac{\text{VH}}{\text{VV}} \) | Ratio of linear backscatter. VV ≤ 0 or non-finite → NaN. |

We do **not** convert the ratio to dB in the stored value; we store the linear ratio. If you need the ratio in dB: \( 10\,\log_{10}(\text{VH}/\text{VV}) = \text{VH\_dB} - \text{VV\_dB} \).

**Code reference:** `crop_monitoring/sar_calculator.py` (`compute_vv_db`, `compute_vh_db`, `compute_vh_vv_ratio`, `compute_sar_metrics`).

---

## 6. Land Surface Temperature (LST)

**Source:** Sentinel-3 SLSTR bands **S8** and **S9** (brightness temperatures in **Kelvin**).

### 6.1 LST in Kelvin

\[
\text{LST}_K = T_8 + 1.06\,\Delta T + 0.46\,(\Delta T)^2 + 54.3\,(1 - \varepsilon),\qquad \Delta T = T_8 - T_9
\]

- \( T_8 \), \( T_9 \): S8 and S9 brightness temperatures (K).
- \( \varepsilon \): Emissivity; default **0.97** in code.

### 6.2 LST in Celsius (stored)

\[
\text{LST}_C = \text{LST}_K - 273.15
\]

**Code reference:** `crop_monitoring/temperature_calculator.py` (`lst_kelvin`, `lst_celsius`).

---

## 7. Stored Columns in `operations.crop_indices`

Conceptually, each row includes:

- **Identifiers:** location_id, village, season_id, file_name, date_start, date_end.
- **Field metadata:** area_acre, distance_km, extracted_village, extracted_grower (when present).
- **Indices:** ndvi, ndwi, savi, ndmi, ndre, gci, psri, msavi, evi, lai, lst_celsius.
- **SAR:** vv_db, vh_db, vh_vv_ratio.
- **Master IDs:** grower_id, variety_id (and optional crop_id, crop_name).
- **Geocoded:** town, district, state, country, postcode (when available).

Formulas above define how the numeric index and SAR columns are derived from the raw bands; the pipeline then stores one value per column (polygon mean per observation).

---

## 8. References

- **Pipeline:** `crop_monitoring/pipeline.py`, `scripts/run_crop_analysis_s3_batch.py`
- **Indices:** `crop_monitoring/index_calculator.py`
- **SAR:** `crop_monitoring/sar_calculator.py`
- **LST:** `crop_monitoring/temperature_calculator.py`
- **Data source vs calculated:** `crop_monitoring/DATA_SOURCE_AND_INDICES.md`
- **Schema:** `sql/create_crop_indices_table.sql`, `sql/alter_crop_indices_add_season_id.sql`
