# DOCUMENT COMPLIANCE REPORT
## Crop Monitoring Indices — Audit vs Indices Extraction Guide

**Reference documents:** `Indices_Extraction_Guide.html`, `Indices Extraction.docx`  
**Audit date:** Based on codebase and document content.  
**Scope:** Crop monitoring pipeline (KML → Sentinel → indices → `operations.crop_indices`).

---

## STEP 1 — Requirements Extracted from the Document

From **Indices_Extraction_Guide.html**:

| Requirement | Document specification |
|-------------|-------------------------|
| **Satellites** | Sentinel-2 (optical), Sentinel-3 (thermal), Sentinel-1 (SAR mentioned in summary only) |
| **Sentinel-2 bands** | B02, B03, B04, B05, B08, B11 (Section 3 "Spectral Bands Required") |
| **Sentinel-3 bands** | S8, S9 (brightness temperature Kelvin) |
| **Indices** | NDVI, SAVI, NDMI, MSAVI, NDRE, GCI, PSRI, EVI, LAI, LST |
| **Data extraction** | Process API (evalscript); polygon-based; date range; cloud filter |
| **Cloud filtering** | ≤ 20% (Section 5 Phase 2) |
| **Polygon** | KML upload / polygon-based querying |
| **Date range** | Select date/time window (e.g. 30 DAS, 60 DAS) |

**Formulas (Section 4):**

- NDVI = (B8 − B4) / (B8 + B4)  
- NDMI = (B8 − B11) / (B8 + B11)  
- SAVI = ((B8 − B4) / (B8 + B4 + L)) × (1 + L)  
- MSAVI = (2×NIR + 1 − √((2×NIR+1)² − 8×(NIR−Red))) / 2  
- NDRE = (B08 − B05) / (B08 + B05)  
- GCI = (B08 / B03) − 1  
- PSRI = (B04 − B03) / B08  
- EVI = 2.5 × (B08 − B04) / (B08 + 6×B04 − 7.5×B02 + 1)  
- LAI = 3.618 × EVI − 0.118  
- LST(K) = T8 + 1.06×(T8−T9) + 0.46×(T8−T9)² + 54.3×(1−ε); LST(C) = LST(K) − 273.15; ε = 0.97  

---

## STEP 2 — Satellite Usage Verification

| Satellite | Document | Implementation | Status |
|-----------|----------|----------------|--------|
| **Sentinel-2 L2A** | Optical vegetation | `sentinel_client.py`: DataCollection.SENTINEL2_L2A, evalscript B02,B03,B04,B05,B08,B11 | ✓ Compliant |
| **Sentinel-3 SLSTR** | Thermal LST (S8, S9) | `sentinel_client.py`: SENTINEL3_SLSTR, EVALSCRIPT_S3 returns S8, S9 | ✓ Compliant |
| **Sentinel-1 SAR** | Mentioned in summary | Not used in crop indices pipeline | ⚠ N/A (not required for listed indices) |

**Bands:**

- **Sentinel-2:** Document requires B02, B03, B04, B05, B08, B11. Code uses exactly these (`S2_BANDS` in `sentinel_client.py`). B12 is not in the document’s required list.  
  → ✓ **Fully compliant.**

- **Sentinel-3:** S8, S9. Code requests and uses S8, S9.  
  → ✓ **Fully compliant.**

---

## STEP 3 — Agricultural Indices Verification

| Index | Bands in code | Formula in code | Document formula | Status |
|-------|----------------|----------------|------------------|--------|
| **NDVI** | B08, B04 | `(NIR-Red)/(NIR+Red)` | (B8−B4)/(B8+B4) | ✓ |
| **SAVI** | B08, B04, L=0.5 | `((NIR-Red)/(NIR+Red+L))*(1+L)` | ((B8−B4)/(B8+B4+L))×(1+L) | ✓ |
| **NDMI** | B08, B11 | `(NIR-SWIR)/(NIR+SWIR)` | (B8−B11)/(B8+B11) | ✓ |
| **MSAVI** | B08, B04 | `(2*NIR+1 - sqrt((2*NIR+1)^2 - 8*(NIR-Red)))/2` | Same | ✓ |
| **NDRE** | B08, B05 | `(NIR-RedEdge)/(NIR+RedEdge)` | (B08−B05)/(B08+B05) | ✓ |
| **GCI** | B08, B03 | `(NIR/Green)-1` | (B08/B03)−1 | ✓ |
| **PSRI** | B04, B03, B08 | `(Red-Green)/NIR` | (B04−B03)/B08 | ✓ |
| **EVI** | B08, B04, B02 | `2.5*(NIR-Red)/(NIR+6*Red-7.5*Blue+1)` | Same | ✓ |
| **LAI** | From EVI | `3.618*EVI - 0.118` | Same | ✓ |
| **LST** | S8, S9 | `LST_K = T8+1.06*dT+0.46*dT^2+54.3*(1-ε)`; `LST_C = LST_K-273.15`; ε=0.97 | Same (Sobrino split-window) | ✓ |

**Files checked:**

- `crop_monitoring/index_calculator.py`: ndvi, savi, ndmi, ndre, gci, psri, msavi, evi, lai_from_evi — formulas and bands match document.
- `crop_monitoring/temperature_calculator.py`: lst_kelvin, lst_celsius, ε=0.97 — matches document.
- `crop_monitoring/sentinel_client.py`: evalscript NDVI/SAVI/NDMI use same formulas (B08,B04,B11); pipeline prefers API output when present.

**Normalization / masking:** Reflectance masked for valid pixels (e.g. ≥ 1e-9, finite); indices use safe division and clipping where appropriate.  
→ **Index formulas and bands: ✓ Fully compliant.**

---

## STEP 4 — Pipeline Architecture Verification

| Stage | Document expectation | Implementation | File / location |
|-------|------------------------|-----------------|------------------|
| 1. KML parsing | Extract polygon coordinates | `parse_kml()`: coordinates from KML, GeoJSON geometry | `kml_parser.py` |
| 2. Polygon geometry | Valid geometry handling | `shape(geojson)`, `make_valid`, GeometryCollection→Polygon | `kml_parser.py`, `pipeline.py` |
| 3. Sentinel API request | start_date, end_date | `fetch_band_data(path, start_date, end_date)`; timeRange in input | `band_extractor.py`, `sentinel_client.py` |
| 4. Cloud filtering | Cloud filter (doc: ≤20%) | `maxCloudCoverage` in S2 input (default 50, retry 80) | `sentinel_client.py`, `pipeline.py` |
| 5. Band extraction | B02–B11, S8, S9 | S2: 6 bands + NDVI/SAVI/NDMI; S3: S8, S9 | `sentinel_client.py` |
| 6. Pixel masking | Valid pixels only | `_mask_invalid()` (≥1e-9, finite); valid_nr / valid_ndmi for indices | `pipeline.py` |
| 7. Index computation | All 10 indices | `compute_all_indices()` + LST from S8/S9 | `index_calculator.py`, `temperature_calculator.py`, `pipeline.py` |
| 8. Mean for polygon | Mean index per AOI | `_nanmean_safe()` per index array | `pipeline.py` |
| 9. Database storage | Table for results | `insert_crop_indices()` → `operations.crop_indices` | `repository.py`, `pipeline.py` |

→ **Pipeline stages: ✓ Fully compliant** (all stages present and used).

---

## STEP 5 — Scalability Verification

| Requirement | Implementation | Status |
|-------------|-----------------|--------|
| Multiple KML files | `run_crop_analysis_batch.py` iterates over directory | ✓ |
| Batch processing | `scripts/run_crop_analysis_batch.py` with `--dir` | ✓ |
| Automated execution | Single script per file or batch; no manual steps | ✓ |
| Large-scale processing | Batch continues on per-file failure; summary report | ✓ |

→ **Scalability: ✓ Fully compliant.**

---

## STEP 6 — Identified Issues / Gaps

### 1. Cloud filtering (document: ≤ 20%)

- **Document:** “Set cloud cover filter to ≤ 20%”.
- **Code:** `fetch_s2_bands` default `maxcc=50`; pipeline uses 50 and retries with 80 if no valid data.
- **Location:** `sentinel_client.py` (default 50), `pipeline.py` (50/80).
- **Suggested fix (optional):** If strict doc compliance is required, use default `maxcc=20` and keep retry at 50 or 80 only when the first request returns no valid pixels. Current behaviour prioritizes robustness over strict 20% limit.

### 2. B12 band

- **Task list:** “Expected Sentinel-2 bands: … B12”.
- **Document (HTML):** Required bands table lists only B02, B03, B04, B05, B08, B11. B12 is not required for the indices in the document.
- **Code:** Does not request B12.
- **Conclusion:** No change needed for document compliance. If a future spec requires B12, add it to the evalscript and band list.

### 3. Sentinel-1 (SAR)

- **Document:** Mentioned in Executive Summary as a data source.
- **Code:** No Sentinel-1 in crop indices pipeline.
- **Conclusion:** Document does not define any crop indices from SAR; pipeline is aligned with the indices actually specified. SAR would be a separate, future extension.

---

## STEP 7 — Final Report

### DOCUMENT COMPLIANCE REPORT

| Criterion | Status | Notes |
|-----------|--------|-------|
| **Satellite support** | ✓ Fully compliant | Sentinel-2 L2A and Sentinel-3 SLSTR used as in document. Sentinel-1 not required for listed indices. |
| **Bands extraction** | ✓ Fully compliant | S2: B02, B03, B04, B05, B08, B11. S3: S8, S9. Matches document. |
| **Index formulas** | ✓ Fully compliant | NDVI, SAVI, NDMI, MSAVI, NDRE, GCI, PSRI, EVI, LAI match document; correct bands and expressions. |
| **LST algorithm** | ✓ Fully compliant | Sobrino split-window; LST(K) and LST(C); ε=0.97. Implemented in `temperature_calculator.py`. |
| **Polygon queries** | ✓ Fully compliant | KML → polygon → geometry in API request. |
| **Date range queries** | ✓ Fully compliant | start_date / end_date passed to Process API timeRange. |
| **Cloud filtering** | ⚠ Partially compliant | Document recommends ≤20%. Code uses 50% default with 80% retry for robustness. Filter is applied. |
| **Batch processing** | ✓ Fully compliant | Batch script, multiple KMLs, continues on failure, summary report. |

---

### Summary

- The implementation is **fully aligned** with the Indices Extraction Guide for satellites, bands, index formulas (including LST), polygon and date handling, and batch processing.
- The only **partial** compliance is cloud filtering: the document recommends ≤20% cloud cover; the code uses a higher default (50%) and retry (80%) to improve data availability, while still applying a cloud filter.

**Reference implementation files:**

- `crop_monitoring/kml_parser.py` — KML parsing, polygon/GeoJSON.
- `crop_monitoring/sentinel_client.py` — S2/S3 requests, bands, evalscript (NDVI/SAVI/NDMI).
- `crop_monitoring/band_extractor.py` — Band fetch orchestration.
- `crop_monitoring/index_calculator.py` — Vegetation/moisture indices.
- `crop_monitoring/temperature_calculator.py` — LST.
- `crop_monitoring/pipeline.py` — End-to-end pipeline, masking, means, DB.
- `crop_monitoring/database/repository.py` — `insert_crop_indices`, `operations.crop_indices`.
- `scripts/run_crop_analysis.py` — Single KML.
- `scripts/run_crop_analysis_batch.py` — Batch processing.
