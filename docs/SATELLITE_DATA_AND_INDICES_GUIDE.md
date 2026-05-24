    # Satellite data, bands, and indices — SeedIQ crop monitoring

This guide explains **how we obtain Earth observation data** (from satellite to your Excel/DB) and **every band and index** the system extracts today. For exact formulas, see [CROP_INDICES_EXTRACTION_AND_FORMULAS.md](./CROP_INDICES_EXTRACTION_AND_FORMULAS.md). For a compact table-only reference, see [INDICES_AND_BANDS_REFERENCE.md](./INDICES_AND_BANDS_REFERENCE.md).

---

## 1. How we get data “directly from satellites”

We do **not** download full satellite scenes to our servers. Instead, we use **authorized access to the same official products** the satellites feed into the ground segment—via **Copernicus Data Space Ecosystem (CDSE)** and **Sentinel Hub** APIs.

### 1.1 End-to-end path

| Step | What happens |
|------|----------------|
| **1. Acquisition** | **Sentinel-2**, **Sentinel-3 SLSTR**, and **Sentinel-1** collect measurements over Earth on their orbits (optical reflectance, thermal brightness temperature, radar backscatter). |
| **2. Downlink & processing** | Data are received at ground stations, calibrated, and turned into standard **products** (e.g. Sentinel-2 **L2A** bottom-of-atmosphere reflectance, Sentinel-1 **GRD** ground-range-detected radar). |
| **3. Archive** | Those products are stored in **Copernicus** archives (the same data scientists use worldwide). |
| **4. Sentinel Hub on CDSE** | **Sentinel Hub** connects to that archive. When we send a request, the Hub **selects pixels** that fall inside our **field polygon** and **time window**, applies **cloud filters** (for optical data), and runs our **evalscript** (band math) **on the fly**. |
| **5. Our application** | SeedIQ calls Sentinel Hub with **OAuth credentials** (`SH_CLIENT_ID` / `SH_CLIENT_SECRET`), a **GeoJSON polygon** (from KML), and **dates**. The Hub returns either **raster arrays** (Process API) or **daily statistics** (Statistical API). |

So “direct from satellites” means: **the values come from official satellite-derived products**, computed for your exact AOI and date range through **Copernicus-backed APIs**—not from a third-party reinterpretation of unrelated data.

### 1.2 Why two APIs?

| API | Role in SeedIQ | Typical output |
|-----|----------------|----------------|
| **Process API** | One (or few) requests per field and date range. | **GeoTIFF-like rasters**: per-pixel bands (and some indices) clipped to the polygon bounding box at chosen resolution (e.g. 10 m for S2/S1). Used for **snapshot bands**, **SAR (VV/VH)**, **thermal (S8/S9 → LST)**. Implemented in `crop_monitoring/sentinel_client.py`. |
| **Statistical API** | One request for a **whole date range**. | **Daily polygon means** for multiple indices (no full raster per day stored locally). Used for **time series** (e.g. Excel calendar rows, trend charts). Implemented in `crop_monitoring/statistical_client.py`. |

Both APIs run **evalscripts** against the **same Sentinel-2 L2A** (and separate collections for S1/S3) data in the archive.

### 1.3 Authentication & endpoint

- **Base URL:** `https://sh.dataspace.copernicus.eu` (CDSE Sentinel Hub).
- **Token URL:** Copernicus identity service (OAuth client credentials).
- **Required env/settings:** `SH_CLIENT_ID`, `SH_CLIENT_SECRET` (and optionally `SH_INSTANCE_ID` if used by your deployment).

Without valid credentials, requests fail before any satellite data is returned.

---

## 2. Satellites and products we use

| Mission | Product | What we read | Main use |
|---------|---------|--------------|----------|
| **Sentinel-2** | **L2A** | Surface reflectance bands **B02, B03, B04, B05, B08, B11** | Vegetation, moisture, red-edge indices; cloud-aware mosaicking (`maxCloudCoverage`, least-cloud order). |
| **Sentinel-3** | **SLSTR** (S8, S9) | **Brightness temperature (K)** | Land surface temperature (LST) after combination in `temperature_calculator.py`. |
| **Sentinel-1** | **GRD** (IW, VV+VH) | **Radar backscatter** (linear) | SAR means, dB, VH/VV ratio in pipeline / DB. |

---

## 3. Raw bands and products extracted

These are the **actual measurement channels** pulled from the Hub for the crop pipeline.

| Source | Band / product | Description (typical) | Fetched via |
|--------|----------------|----------------------|-------------|
| Sentinel-2 L2A | **B02** | Blue ~490 nm | Process API + Statistical (as input to indices) |
| Sentinel-2 L2A | **B03** | Green ~560 nm | Same |
| Sentinel-2 L2A | **B04** | Red ~665 nm | Same |
| Sentinel-2 L2A | **B05** | Red edge ~705 nm | Same |
| Sentinel-2 L2A | **B08** | NIR ~842 nm | Same |
| Sentinel-2 L2A | **B11** | SWIR1 ~1610 nm | Same + Excel `SENT2_B11` |
| Sentinel-2 L2A | **B12** | SWIR2 ~2190 nm | Excel `SENT2_B12` (Hub resamples 20 m SWIR to query resolution) |
| Sentinel-2 L2A | **NDVI, SAVI, NDMI** | Same formulas as below; extra **output bands** in Process API evalscript | Process API only (`sentinel_client.py`) |
| Sentinel-3 SLSTR | **S8** | BT nadir (K) | Process API |
| Sentinel-3 SLSTR | **S9** | BT nadir (K) | Process API |
| Sentinel-1 GRD | **VV** | Co-polar backscatter (linear) | Process API |
| Sentinel-1 GRD | **VH** | Cross-polar backscatter (linear) | Process API |

**Note:** The main pipeline does **not** use B8A, B12, or SCL in `crop_monitoring/` (some legacy paths under `services/` may differ).

---

## 4. Indices and derived metrics

### 4.1 From Sentinel-2 (vegetation, moisture, structure)

| Index | Inputs (bands) | Where produced |
|-------|----------------|----------------|
| **NDVI** | B08, B04 | Statistical API; Process API evalscript; `index_calculator`; Excel |
| **NDWI** | B03, B08 (McFeeters) | Statistical API; Excel |
| **NDWI_GAO** | B08, B11 (Gao: same as NDMI) | Excel from `SENT2_B08` / `SENT2_B11` |
| **SAVI** | B08, B04 (L = 0.5) | Statistical API; Process API; pipeline; Excel |
| **NDMI** | B08, B11 | Statistical API; Process API; pipeline; Excel |
| **NDRE** | B08, B05 | Statistical API; pipeline; Excel |
| **GCI** | B08, B03 — (NIR/Green) − 1 | Statistical API; pipeline |
| **PSRI** | B04, B03, B08 | Statistical API; pipeline; Excel |
| **MSAVI** | B08, B04 | Statistical API; pipeline; Excel |
| **EVI** | B08, B04, B02 | Statistical API; pipeline; Excel |
| **LAI** | From **EVI**: 3.618×EVI − 0.118 | Statistical API (post); pipeline; Excel |
| **GNDVI** | B08, B03 | Excel export (`export_kml_to_excel.py`) |
| **ARVI** | B08, B04, B02 | Excel export only |
| **VARI** | B03, B04, B02 | Excel export only |
| **CCCI** | NDRE / NDVI | Excel export only |

### 4.2 SAR (Sentinel-1)

| Metric | Source | Notes |
|--------|--------|--------|
| **VV**, **VH** | Linear backscatter | Polygon means from Process API rasters |
| **VV_dB**, **VH_dB** | 10·log₁₀(VV/VH) | `sar_calculator.py` |
| **vh_vv_ratio** | VH / VV | `sar_calculator.py` |

### 4.3 Thermal (Sentinel-3)

| Metric | Source | Notes |
|--------|--------|--------|
| **LST (K)** | S8, S9 | `temperature_calculator.lst_kelvin` |
| **lst_celsius** | LST − 273.15 | Pipeline, DB, Excel |

---

## 5. Statistical API vs Process API — index outputs

| API | Sentinel data | What you get back |
|-----|---------------|-------------------|
| **Statistical** | S2 L2A, daily step **P1D** | Per day, polygon **mean** of: **NDVI, SAVI, NDMI, NDRE, GCI, PSRI, MSAVI, EVI, NDWI**, plus **LAI** from EVI. |
| **Process** | S2 L2A (one composite over interval) | Rasters: **B02–B05, B08, B11** + **NDVI, SAVI, NDMI**; plus separate calls for **S8/S9**, **VV/VH**. |

SAR and LST are **not** part of the Statistical API evalscript; they come from **Process API** requests in `sentinel_client.py`.

---

## 6. Excel export alignment (`scripts/export_kml_to_excel.py`)

Per row (calendar / daily), typical numeric columns include:

- **S2 band means:** Red (B04), NIR (B08), Green (B03), Blue (B02)  
- **SAR:** VV, VH (linear)  
- **Thermal:** lst_celsius  
- **Indices:** NDVI, NDWI, NDMI, EVI, SAVI, MSAVI, GNDVI, ARVI, VARI, PSRI, LAI, NDRE, CCCI  

Daily S2-backed indices primarily follow the **Statistical API**; GNDVI, ARVI, VARI, CCCI, and LAI (from EVI) are filled in the export script where applicable.

---

## 7. Code map

| Topic | File(s) |
|-------|---------|
| S2 / S1 / S3 Process API | `crop_monitoring/sentinel_client.py` |
| Daily S2 index statistics | `crop_monitoring/statistical_client.py` |
| Fetch bands for KML | `crop_monitoring/band_extractor.py` |
| Index math (numpy) | `crop_monitoring/index_calculator.py` |
| SAR dB / ratio | `crop_monitoring/sar_calculator.py` |
| LST | `crop_monitoring/temperature_calculator.py` |
| End-to-end run | `crop_monitoring/pipeline.py`, `scripts/run_crop_analysis_s3_batch.py` |
| Excel workbook | `scripts/export_kml_to_excel.py` |

---

## 8. Related documents

| Document | Purpose |
|----------|---------|
| [INDICES_AND_BANDS_REFERENCE.md](./INDICES_AND_BANDS_REFERENCE.md) | Quick tables, API comparison |
| [CROP_INDICES_EXTRACTION_AND_FORMULAS.md](./CROP_INDICES_EXTRACTION_AND_FORMULAS.md) | Formulas and pipeline narrative |
| [CROP_MONITORING_PIPELINE_SUMMARY.md](./CROP_MONITORING_PIPELINE_SUMMARY.md) | High-level pipeline |

---

*Document reflects the in-repo crop monitoring stack (CDSE Sentinel Hub, Process + Statistical APIs). Last aligned with `sentinel_client.py`, `statistical_client.py`, and Excel export behavior.*
