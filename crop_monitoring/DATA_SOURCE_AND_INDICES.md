# Data source vs calculated indices

## Priority: API when available, else backend formulas

We **prefer** getting indices directly from the Copernicus / Sentinel Hub Process API when the API can compute them. For **NDVI, SAVI, NDMI** we request them from the API (computed in the evalscript). If the API does not return them, we fall back to calculating them in our backend using the same formulas.

---

## From Copernicus (direct from API)

| Source          | What we get | Description |
|-----------------|-------------|-------------|
| Sentinel-2 L2A  | **B02, B03, B04, B05, B08, B11** | Reflectance (0–1) per pixel |
| Sentinel-2 L2A  | **NDVI, SAVI, NDMI** (from evalscript) | Same formulas as below; computed per pixel in API |
| Sentinel-3 SLSTR| **S8, S9**  | Brightness temperature (Kelvin) per pixel |

The Process API evalscript returns 9 bands: 6 reflectance bands + NDVI, SAVI, NDMI. So we get these three indices **directly from the API** (one request, no extra cost). Backend uses them when present; otherwise it computes them from bands.

---

## How NDVI, SAVI, NDMI are defined (API and backend)

Same formulas in both places:

| Index | Formula | Bands |
|-------|---------|--------|
| **NDVI** | `(NIR − Red) / (NIR + Red)` | B08 (NIR), B04 (Red) |
| **SAVI** | `((NIR − Red) / (NIR + Red + 0.5)) × 1.5` | B08, B04 |
| **NDMI** | `(NIR − SWIR) / (NIR + SWIR)` | B08 (NIR), B11 (SWIR) |

- **From API:** Evalscript in `crop_monitoring/sentinel_client.py` computes these per pixel and returns them as bands 6, 7, 8.
- **From backend:** `crop_monitoring/index_calculator.py` computes them from the same bands if not provided by the API.

---

## Calculated only in our pipeline (backend)

These indices are **not** returned by the API; we always compute them from bands in `index_calculator.py` and `temperature_calculator.py`:

| Index | Formula / source |
|-------|------------------|
| **NDRE**  | `(NIR − RedEdge) / (NIR + RedEdge)` — B08, B05 |
| **GCI**   | `(NIR / Green) − 1` — B08, B03 |
| **PSRI**  | `(Red − Green) / NIR` — B04, B03, B08 |
| **MSAVI** | `(2×NIR + 1 − sqrt((2×NIR+1)² − 8×(NIR−Red))) / 2` — B08, B04 |
| **EVI**   | `2.5 × (NIR − Red) / (NIR + 6×Red − 7.5×Blue + 1)` — B08, B04, B02 |
| **LAI**   | `3.618 × EVI − 0.118` — from EVI |
| **LST_C** | From S8, S9 (Sentinel-3) — `LST_K − 273.15` |

---

## Summary

- **From API (priority):** 6 optical bands + **NDVI, SAVI, NDMI** (computed in evalscript).
- **From backend:** NDVI, SAVI, NDMI only when not in API response; **NDRE, GCI, PSRI, MSAVI, EVI, LAI** always; **LST_C** from S3 in `temperature_calculator.py`.
