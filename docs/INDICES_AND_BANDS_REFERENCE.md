# Indices and bands — current extraction reference

This document lists **every raw band/product** pulled from satellites and **every index or derived metric** produced in SeedIQ crop monitoring today. For full math and pipeline narrative, see [CROP_INDICES_EXTRACTION_AND_FORMULAS.md](./CROP_INDICES_EXTRACTION_AND_FORMULAS.md).

---

## 1. Raw bands and products extracted

| Source | Product / band ID | Role (typical) | Where fetched |
|--------|-------------------|----------------|---------------|
| **Sentinel-2 L2A** | **B02** | Blue (~490 nm) | Process API (`sentinel_client`), Statistical API (`statistical_client`), Excel time series |
| **Sentinel-2 L2A** | **B03** | Green (~560 nm) | Same |
| **Sentinel-2 L2A** | **B04** | Red (~665 nm) | Same |
| **Sentinel-2 L2A** | **B05** | Red edge (~705 nm) | Same |
| **Sentinel-2 L2A** | **B08** | NIR (~842 nm) | Same |
| **Sentinel-2 L2A** | **B11** | SWIR1 (~1610 nm) | Process API, Excel `SENT2_B11` |
| **Sentinel-2 L2A** | **B12** | SWIR2 (~2190 nm) | Process API, Excel `SENT2_B12` |
| **Sentinel-2 L2A** *(optional, Process API only)* | **NDVI, SAVI, NDMI** | Same formulas as below; returned as extra “bands” in evalscript | `crop_monitoring/sentinel_client.py` (evalscript returns 9 floats: 6 reflectance + 3 indices) |
| **Sentinel-3 SLSTR** | **S8** | Brightness temperature (K), nadir | `sentinel_client` → LST |
| **Sentinel-3 SLSTR** | **S9** | Brightness temperature (K), nadir | `sentinel_client` → LST |
| **Sentinel-1 GRD** | **VV** | Co-polar backscatter (linear) | `sentinel_client` |
| **Sentinel-1 GRD** | **VH** | Cross-polar backscatter (linear) | `sentinel_client` |

**Summary — optical reflectance used in production:** **B02, B03, B04, B05, B08, B11** (six S2 bands). No B8A, B12, or SCL in the main crop pipeline (those appear only in legacy/alternate modules such as `services/index_calculator.py` / `services/band_extractor.py`).

---

## 2. Indices and metrics derived from S2 (vegetation / moisture / color)

These are computed either in the **Statistical API evalscript** (daily means per polygon), in **`crop_monitoring/index_calculator.py`** (pixel-wise then polygon mean), or in **Excel export** post-processing. Formulas match [CROP_INDICES_EXTRACTION_AND_FORMULAS.md](./CROP_INDICES_EXTRACTION_AND_FORMULAS.md) unless noted.

| Name | Inputs (bands) | Produced where |
|------|----------------|----------------|
| **NDVI** | B08, B04 | Statistical API; pipeline; Excel |
| **NDWI** | B03, B08 | Statistical API; Excel |
| **SAVI** | B08, B04 (L = 0.5) | Statistical API; pipeline; Excel |
| **NDMI** | B08, B11 | Statistical API; pipeline; Excel |
| **NDRE** | B08, B05 | Statistical API; pipeline; Excel |
| **GCI** | B08, B03 — (NIR/Green) − 1 | Statistical API; pipeline / DB — **not** a separate Excel column (values still flow via API/pipeline) |
| **PSRI** | B04, B03, B08 | Statistical API; pipeline; Excel |
| **MSAVI** | B08, B04 | Statistical API; pipeline; Excel |
| **EVI** | B08, B04, B02 | Statistical API; pipeline; Excel |
| **LAI** | From **EVI**: 3.618×EVI − 0.118 | Pipeline; Excel (from EVI, not from NDVI) |
| **GNDVI** | B08, B03 — (NIR−Green)/(NIR+Green) | Excel export only (`export_kml_to_excel.py`) |
| **ARVI** | B08, B04, B02 — atmospheric-resistant VI (γ=1) | Excel export only |
| **VARI** | B03, B04, B02 — visible atmospherically resistant index | Excel export only |
| **CCCI** | NDRE / NDVI (canopy chlorophyll) | Excel export only (NDVI magnitude guarded) |

---

## 3. SAR metrics (Sentinel-1)

| Name | Source | Notes |
|------|--------|--------|
| **VV**, **VH** | Linear backscatter from Process API | Stored / exported as means over polygon |
| **VV_dB** | 10·log₁₀(VV) | Pipeline / DB (`sar_calculator`) |
| **VH_dB** | 10·log₁₀(VH) | Pipeline / DB |
| **vh_vv_ratio** | VH / VV (linear) | Pipeline / DB |

Excel export uses **VV** and **VH** (linear means) as columns alongside indices.

---

## 4. Thermal

| Name | Source | Notes |
|------|--------|--------|
| **LST (K)** | S8, S9 | `temperature_calculator.lst_kelvin` |
| **lst_celsius** | LST_K − 273.15 | Pipeline, DB, Excel |

---

## 5. Excel export column alignment (`scripts/export_kml_to_excel.py`)

Per calendar row, numeric fields include (among metadata):

- **Band means (S2):** Red (B04), NIR (B08), Green (B03), Blue (B02)
- **SAR:** VV, VH (linear)
- **Thermal:** lst_celsius
- **Indices:** NDVI, NDWI, NDMI, EVI, SAVI, MSAVI, GNDVI, ARVI, VARI, PSRI, LAI, NDRE, CCCI

Daily S2-backed indices come from the **Statistical API** where available; GNDVI, ARVI, VARI, CCCI, and LAI (from EVI) are filled in the export script.

---

## 6. Statistical API vs Process API (indices)

| API | What it returns |
|-----|-----------------|
| **Statistical API** (`statistical_client.py`) | Daily polygon means for: **NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, NDWI** (9 bands from one evalscript). |
| **Process API** (`sentinel_client.py`) | Per-request rasters: S2 **B02–B05, B08, B11** + **NDVI, SAVI, NDMI**; S3 **S8, S9**; S1 **VV, VH**. |

---

## 7. Code map

| Topic | Primary files |
|-------|----------------|
| S2/S1/S3 fetch | `crop_monitoring/sentinel_client.py` |
| Daily index stats | `crop_monitoring/statistical_client.py` |
| Index math (numpy) | `crop_monitoring/index_calculator.py` |
| SAR dB / ratio | `crop_monitoring/sar_calculator.py` |
| LST | `crop_monitoring/temperature_calculator.py` |
| End-to-end run | `crop_monitoring/pipeline.py`, `scripts/run_crop_analysis_s3_batch.py` |
| Excel workbook | `scripts/export_kml_to_excel.py` |
| Alternate S2 stack (8 bands incl. B8A, B12, SCL) | `services/index_calculator.py` (not the main crop pipeline) |

---

*Last updated to reflect crop monitoring + Excel export as implemented in-repo. For formula details, see CROP_INDICES_EXTRACTION_AND_FORMULAS.md.*
