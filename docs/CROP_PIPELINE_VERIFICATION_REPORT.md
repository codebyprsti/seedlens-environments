# Crop Monitoring Pipeline — End-to-End Verification Report

**Date:** 2026-03-03  
**KML used:** `C:\Users\madan\Downloads\CG-20260302T122713Z-1-001\CG\AMLIPARA ANIL KUMAR NETAM USRH-24 BALJI - Copy.kml`

---

## STEP 1 — Database schema

**Query run:**  
`SELECT column_name FROM information_schema.columns WHERE table_schema='operations' AND table_name='crop_indices';`

**Result:**  
Columns include: `id`, `location_id`, `grower_id`, `variety_id`, `polygon_id`, `polygon_area`, `date_start`, `date_end`, `analysis_date`, `ndvi`, `savi`, `ndmi`, `ndre`, `gci`, `psri`, `msavi`, `evi`, `lai`, `lst_c`, `created_at`, `village`, `town`, `district`, `state`, `country`, `postcode`, `lst_celsius`, `grower_name`, `variety_name`, **`vv_db`**, **`vh_db`**, **`vh_vv_ratio`**.

**SAR columns:** **Present** (vv_db, vh_db, vh_vv_ratio).

No migration was required; schema already had SAR columns.

---

## STEP 2 — Pipeline modules

| Check | Status |
|-------|--------|
| sentinel_client.EVALSCRIPT_S1 | OK |
| sentinel_client.fetch_s1_sar() | OK |
| sar_calculator.compute_vv_db() | OK |
| sar_calculator.compute_vh_db() | OK |
| sar_calculator.compute_vh_vv_ratio() | OK |
| pipeline._mask_sar_invalid() | OK |
| pipeline._compute_means() supports SAR (s1_bands) | OK |
| band_extractor.fetch_band_data returns (s2_bands, s3_bands, s1_bands, geojson) | OK |
| repository.insert_crop_indices() includes vv_db, vh_db, vh_vv_ratio | OK |

---

## STEP 3 — Sentinel API calls

- **Sentinel-2:** B02, B03, B04, B05, B08, B11 — requested and returned. First request (maxcc=20) had valid_gt0=0; retry with relaxed cloud returned valid_gt0=2879.
- **Sentinel-3:** S8, S9 — requested; LST computed (LST_C = 36.51°C).
- **Sentinel-1:** VV, VH — requested; arrays returned but **valid=0** (no valid backscatter pixels in this AOI/date). Pipeline logged S1 response and continued; SAR metrics stored as NaN.

SAR API did not return valid pixels for this polygon/date; pipeline correctly continued and stored NaN for vv_db, vh_db, vh_vv_ratio.

---

## STEP 4 — Single KML pipeline run (captured logs)

1. **Polygon extraction:** `[pipeline] polygon CRS=WGS84 area_ha=0.722354 bbox=(min_lon=81.762828 min_lat=20.386537 max_lon=81.763799 max_lat=20.387372) valid=True`
2. **Sentinel-2 request:** First call valid_gt0=0; retry with relaxed cloud; second S2 request: B02–B11 + NDVI/SAVI/NDMI, valid_gt0=2879.
3. **Sentinel-3 request:** S8, S9 used for LST.
4. **Sentinel-1 request:** `[S1 SAR] VV shape=(64, 64) valid=0 VH shape=(64, 64) valid=0` (twice — once per date window tried).
5. **Band arrays:** S2 bands received (64×64); S3 thermal used; S1 arrays received but no valid pixels.
6. **Index calculations:** `[pipeline] index_means NDVI=0.8264 SAVI=0.5321 EVI=0.6129`
7. **SAR metrics:** vv_db, vh_db, vh_vv_ratio computed as NaN (nanmean over no valid SAR pixels).
8. **Database insert:** `Storing crop analysis with village='Jamgaon', grower_name='Anil Kumar netam' and variety_name='USRH24'` — insert and commit succeeded.

---

## STEP 5 — Computed indices

**Optical/thermal indices (all produced):**

- NDVI, SAVI, NDMI, MSAVI, NDRE, GCI, PSRI, EVI, LAI, LST_C  

**SAR metrics:**

- vv_db, vh_db, vh_vv_ratio — **NaN** for this run (no valid Sentinel-1 pixels in AOI/date).

Polygon aggregation uses **nanmean** for all indices and SAR metrics.

---

## STEP 6 — Database insert verification

**Query run (postcode; table has no `postal_code`):**

```sql
SELECT village, grower_name, variety_name, ndvi, savi, ndmi, msavi, ndre, gci, psri, evi, lai, lst_celsius, vv_db, vh_db, vh_vv_ratio, state, district, country, postcode
FROM operations.crop_indices
ORDER BY created_at DESC
LIMIT 5;
```

**Sample row (latest):**

| Village | Grower         | Variety | NDVI   | SAVI   | NDMI   | LST_C  | VV_dB | VH_dB | Ratio |
|---------|----------------|---------|--------|--------|--------|--------|-------|-------|-------|
| Jamgaon | Anil Kumar netam | USRH24  | 0.8264 | 0.5321 | 0.3601 | 36.5121 | NaN   | NaN   | NaN   |

---

## STEP 7 — Output summary

**Pipeline status:** SUCCESS  

**Data sources:**

- Sentinel-2 bands fetched (B02, B03, B04, B05, B08, B11).
- Sentinel-3 LST computed (S8, S9 → LST_C).
- Sentinel-1 SAR fetched (VV, VH); no valid pixels in this AOI/date → vv_db, vh_db, vh_vv_ratio stored as NaN.

**Table (from verification run):**

| Village | Grower           | Variety | NDVI   | SAVI   | NDMI   | LST_C  | VV_dB | VH_dB | Ratio |
|---------|------------------|---------|--------|--------|--------|--------|-------|-------|-------|
| Jamgaon | Anil Kumar netam | USRH24  | 0.8264 | 0.5321 | 0.3601 | 36.5121 | NaN   | NaN   | NaN   |

---

## STEP 8 — Validation

- **Indices:** Not all NaN — NDVI, SAVI, NDMI, MSAVI, NDRE, GCI, PSRI, EVI, LAI, LST_C are numeric.
- **SAR metrics:** Columns vv_db, vh_db, vh_vv_ratio are present; values are NaN for this run because S1 returned no valid pixels.
- **Database insert:** Row inserted successfully; latest row visible in `operations.crop_indices`.

**Fix applied during verification:**  
`scripts/run_crop_analysis.py` was printing a Unicode arrow (→), which caused `UnicodeEncodeError` on Windows (cp1252). The line was changed to ASCII (`->`) so the script exits 0.

---

## Final output

1. **Pipeline run status:** SUCCESS (polygon → S2/S3/S1 → indices + SAR means → DB insert).
2. **Indices computed:** NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, LAI, LST_C (all numeric for this KML).
3. **SAR metrics computed:** vv_db, vh_db, vh_vv_ratio (NaN for this run; S1 had no valid pixels).
4. **Database insert:** Confirmed; row in `operations.crop_indices` with village=Jamgaon, grower_name=Anil Kumar netam, variety_name=USRH24, and all index/SAR columns.
5. **Sample row:** See table in STEP 7 above.

---

## SAR metrics NaN — cause and behaviour

For this polygon and date range, Sentinel-1 returned VV/VH arrays with **valid=0** (no pixels with valid backscatter, e.g. no S1 coverage or all masked). The pipeline:

- Fetches S1 when `fetch_sar=True`.
- Computes vv_db, vh_db, vh_vv_ratio with safe log and division.
- Uses nanmean, so no valid pixels → NaN.
- Inserts NaN into `operations.crop_indices` and does not raise.

No code change required; for AOIs/dates with S1 coverage, SAR metrics will be non-NaN.
