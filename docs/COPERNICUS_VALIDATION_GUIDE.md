# Copernicus cross-validation guide (Sentinel-1, Sentinel-2, Sentinel-3)

Use this document to compare values stored in `operations.crop_indices` with what you see in [Copernicus Browser](https://browser.dataspace.copernicus.eu) (or EO Browser on CDSE).

---

## 1. What “coverage area” we use (important)

There are **two different geometries** in this project.

### A) Main crop ingestion (`run_crop_analysis_s3_batch.py` — `crop_indices` mode)

| Source | Geometry | Meaning |
|--------|----------|--------|
| **Sentinel-2 (indices + bands)** | **Full KML polygon** | Statistical API uses the parsed polygon GeoJSON; means are aggregated over the **field polygon**, not a point. |
| **Sentinel-1 (SAR) per `analysis_date`** | **Full KML polygon** | Process API request uses polygon bounds; **10 m** resolution, **at least 64×64 pixels** → ground extent is **at least ~640 m × 640 m** for the *request grid*, clipped/masked by your polygon. |
| **Sentinel-3 (LST) per `analysis_date`** | **Full KML polygon** | Process API: **1000 m** resolution, **at least 2×2 pixels** → very coarse vs the field; values are **polygon-level means** over that grid. |

So for **rows created by the batch script**, validation should use the **same uploaded KML polygon** in Copernicus Browser, not a tiny box around the centroid.

### B) SAR/LST backfill only (`update_sar_lst_backfill.py`)

| Source | Geometry | Approximate footprint |
|--------|----------|------------------------|
| **S1 + S3 (per-date recompute)** | **Square around field centroid** | Default half-side **`0.00045°`** latitude/longitude → full side **`0.0009°`**. At ~21° N that is roughly **~100 m × ~95 m** on the ground (order of magnitude; varies with latitude). |

So for **rows only touched by the backfill script**, SAR/LST reflect that **small square**, not the full parcel. NDVI/S2 in DB still came from the original polygon-based run.

**Takeaway:** When validating **SAR/LST**, know whether the row was filled by **ingestion (polygon)** or **backfill (centroid micro-box)**. For strict apples-to-apples, use the **same AOI** the code used (polygon vs centroid box).

---

## 2. Time windows (per `analysis_date`)

These match `_lst_sar_polygon_means_for_analysis_date` in `scripts/run_crop_analysis_s3_batch.py` (also used by the backfill).

| Sensor | Window (relative to `analysis_date` = `d0`) |
|--------|---------------------------------------------|
| **Sentinel-3 SLSTR (LST)** | `d0 − 1 day` through `d0 + 3 days` (exclusive end on ISO date math as in code) — short pad so single-day passes are not missed. |
| **Sentinel-1 GRD (VV/VH)** | `d0 − 12 days` through `d0 + 1 day` — mosaic over that window; not a single acquisition time. |

**Sentinel-2** in ingestion: season range (e.g. Rabi window) with **daily (P1D)** Statistical API aggregation; each row’s `analysis_date` aligns with that daily interval.

---

## 3. Units stored in the database

| Column | Unit | Notes |
|--------|------|--------|
| `vv_db`, `vh_db` | **Decibels (dB)** | From linear backscatter via `10·log10(linear)` in `compute_sar_metrics`. |
| `vh_vv_ratio` | **Ratio** | From SAR arrays in the pipeline (not dB difference). |
| `lst_celsius` | **°C** | From S8/S9 brightness temperature via `lst_celsius()` in `temperature_calculator`. |
| NDVI / other S2 indices | **Dimensionless** | Typical −1…1 (or similar), from Statistical API / evalscript. |

**Linear from dB (for checks):** `linear = 10^(dB/10)`.

---

## 4. Copernicus Browser — step-by-step

### 4.1 Sign in and open

1. Open [https://browser.dataspace.copernicus.eu](https://browser.dataspace.copernicus.eu).
2. Sign in with your Copernicus Data Space account.

### 4.2 Load your AOI

1. Use **Upload** / **My data** (or equivalent) and upload the **same KML** you used for ingestion, **or** draw a box that matches the **backfill centroid square** if you are validating backfill-only SAR/LST.
2. Zoom to the AOI.

### 4.3 Sentinel-2 L2A (NDVI / bands)

1. Layer / dataset: **Sentinel-2 L2A**.
2. Set **time** to include your row’s `analysis_date` (± a few days) and apply **cloud masking** similar to production (`maxcc` ~20% if you want parity).
3. Use **Visualize** / index (e.g. NDVI) or custom script if needed.
4. Use **statistics / histogram over AOI** (if available) and compare to `ndvi` (and band means if you export them).

Expect **close but not identical** values: different compositing, exact interval, and cloud handling.

### 4.4 Sentinel-1 GRD (VV, VH)

1. Dataset: **Sentinel-1 GRD** (IW, VV+VH as applicable).
2. Set time range to match the **S1 window** above (e.g. 12 days before through 1 day after `analysis_date`).
3. Display in **dB** if the UI offers it; otherwise convert.
4. Compare spatial mean over AOI to `vv_db` / `vh_db`.

Expect differences if Browser uses a different mosaic order or exact acquisition set than Process API.

### 4.5 Sentinel-3 SLSTR (LST / thermal)

1. Dataset: **Sentinel-3 SLSTR** (LST or brightness temperature, depending on layer).
2. Use time window matching **S3 pad** above.
3. Compare AOI statistics to `lst_celsius` (ensure both are **°C**; convert from K if needed: `°C = K − 273.15`).

S3 is **coarse (~1 km)**; small fields will be heavily mixed pixels.

---

## 5. Validation record template (copy to spreadsheet)

| Field | Example | Your value |
|-------|---------|------------|
| `location_id` | IND-OD-600001 | |
| `file_name` | …kml | |
| `analysis_date` | 2026-01-14 | |
| AOI used | Full KML / Centroid 0.0009° box | |
| **DB** `ndvi` | | |
| **Browser** S2 NDVI (mean) | | |
| **DB** `vv_db` | | |
| **Browser** S1 VV dB (mean) | | |
| **DB** `vh_db` | | |
| **Browser** S1 VH dB (mean) | | |
| **DB** `lst_celsius` | | |
| **Browser** S3 LST °C (mean) | | |
| Notes (cloud, gaps) | | |

Repeat for **10–20** samples across states.

---

## 6. Exports already in the repo

- **KML export by `location_id`:** `scripts/export_kmls_by_location_ids.py`
- **CSV export (crop_indices + field_locations):** `scripts/export_crop_indices_csv_by_location_ids.py`
- **CSV with linear SAR:** derived file `crop_indices_by_location_ids_with_linear.csv` (see conversation / regenerate with `10^(dB/10)` columns).

---

## 7. Quick reference — coverage summary

| Stage | S2 AOI | S1 AOI | S3 AOI |
|-------|--------|--------|--------|
| **Ingestion (`crop_indices`)** | Full polygon | Full polygon (10 m, ≥64×64 px grid on bbox) | Full polygon (1000 m, ≥2×2 px) |
| **Backfill (`update_sar_lst_backfill`)** | *(unchanged)* | Centroid square **0.0009°** side (~100 m scale) | Same micro-square |

---

## 8. References in code

- SAR/LST windows: `scripts/run_crop_analysis_s3_batch.py` → `_lst_sar_polygon_means_for_analysis_date`
- S1/S2/S3 Process API resolution: `crop_monitoring/sentinel_client.py` → `fetch_s1_sar`, `fetch_s3_thermal`, `fetch_s2_bands`
- S2 Statistical API: `crop_monitoring/statistical_client.py` → `P1D`, `resolution=10`
- Backfill micro-AOI: `scripts/update_sar_lst_backfill.py` → `_centroid_square_geojson(..., half_size_deg=0.00045)`
