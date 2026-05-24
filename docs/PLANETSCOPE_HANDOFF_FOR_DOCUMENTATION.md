# PlanetScope field validation — handoff for documentation (Claude / internal)

This note is a **single source of truth** for writing user-facing or technical documentation about PlanetScope integration in SeedIQ-Prod. It summarizes **what we built**, **what Planet trial accounts actually allowed in tests**, and **where work stopped** (not assumptions).

---

## 1. Why this exists

The goal was to enrich field-team CSV data with:

- **Sentinel-2** metrics from PostgreSQL (`operations.crop_indices`), where available.
- **PlanetScope (PSScene)** scene metadata and, ideally, **zonal band means** and **vegetation indices** (NDVI, SAVI, EVI, GNDVI, NDRE when bands exist).

Planet exposes **several different APIs** and **contract-level permissions**. A **trial API key** does **not** grant the same capabilities as a paid **Orders + clip + download** workflow. This repo encodes both the **happy path** (metadata) and the **optional** Orders/zonal path, and includes a **permission audit script** so results are **measured**, not guessed.

---

## 2. Blockages at a glance (trial / org limits)

These are **observed HTTP responses** from `scripts/planet_api_permission_audit.py` (see `docs/` references below). Wording may vary slightly by account; re-run the script for your key.

| Capability | Typical trial result (tested) | What it means for documentation |
|------------|-------------------------------|----------------------------------|
| **Data API quick-search** | HTTP **200**, PSScene `item_id`s returned | **Search / catalog** works; you can list scenes over AOI + dates. |
| **GET `/items/{id}/assets/`** | HTTP **200** with **`{}`** empty object | Listing endpoint may return no per-asset keys even when features list asset **names**. |
| **GET …/assets/ortho_analytic_4b** | HTTP **500** (empty error) | Per-asset metadata/activation path **failed** in trial tests. |
| **POST …/activate** | HTTP **404** (HTML Not Found) | Classic **activate + download** Data API path **not usable** as tested. |
| **Orders API, bundle `analytic_udm2`, no clip** | HTTP **400** — `no access to assets` | **Order delivery** of analytic bundles **blocked** for the trial org on test items. |
| **Orders API with `clip` tool** | HTTP **400** — `no permission to run the 'clip' tool` | **Clip** is a **separate entitlement**; trial often **denies** it. |

**Documentation takeaway:** Say clearly that **“PlanetScope integration”** in this project has **two layers**:

1. **Metadata layer (works with search):** scene id, time, cloud, etc.  
2. **Raster delivery layer (often blocked on trial):** Orders, clip, activation, download — **requires Planet contract/support**, not just an API key.

---

## 3. What the codebase implements

### Core modules (`field_validation/`)

| Module | Role |
|--------|------|
| `csv_loader.py` | Loads “Data Entry_Field Team”-style CSV; optional filter on manual SYNC/NDVI columns. |
| `db_queries.py` | Reads `crop_indices` for Sentinel time series. |
| `s3_kml.py` | S3 KML download; `kml_basenames_for_location()` merges DB + CSV basenames. |
| `s3_geometry_probe.py` | Probes geometry resolution for all `location_id`s (orchestration). |
| `planet_api.py` | Data API v1 **quick-search**; optional **asset key** listing; observation-only rows. |
| `planet_orders_zonal.py` | Orders API v2 **clipped** scenes → GeoTIFF → zonal stats (uses **`{"clip": {"aoi": ...}}`** tool shape per Planet docs). |
| `planet_indices.py` | NDVI/SAVI/EVI/GNDVI/NDRE helpers aligned with `crop_monitoring.index_calculator`. |
| `planet_sync_metrics.py` | **Timing** “sync” vs field validation date (not biological SYNC from CSV). |
| `raster_indices.py` | Zonal means from **local** analytic GeoTIFF (4+ bands). |
| `output_builder.py` | Merges CSV + Sentinel + Planet rows into Excel-shaped tables. |
| `date_utils.py` | Parses validation dates like `01-Apr` with a default year. |

### Scripts

| Script | Role |
|--------|------|
| `scripts/run_field_team_planet_validation.py` | Main Excel generator: DB + S3 + Planet metadata; flags for **observations-only**, **Orders zonal**, **drop manual columns**, **strict row filters**. |
| `scripts/planet_zonal_full_pipeline.py` | Orchestrates env check, S3 probe, subprocess run, sample NDVI/sync printout. |
| `scripts/planet_api_permission_audit.py` | **Programmatic permission proof**: quick-search, assets, activation, download, Orders with/without clip. |
| `scripts/verify_planet_index_math.py` | Scalar NDVI check `(NIR-Red)/(NIR+Red)`. |

### Related docs (already in repo)

- `field_validation/PLANET_FIELD_VALIDATION.md` — module overview, CSV semantics.  
- `field_validation/GEOSPATIAL_PLANET_REVIEW.md` — geospatial Q&A (bands, SYNC, NDMI/SWIR).  

Use this handoff doc for **“trial vs paid”** and **audit evidence**; use the others for **field semantics** and **formula** detail.

---

## 4. Environment variables (names only; never commit secrets)

- **`PLANET_API_KEY`** — Planet Data API + Orders (same key in typical setups).  
- **PostgreSQL** — `DATABASE_*` / `core.config` as elsewhere.  
- **AWS** — for S3 KML fetch (`prsti-public-data`, prefixes under `seedworks/kml_files/...`).  

Document that **trial keys** must be **rotated** if exposed (e.g. pasted in chat).

---

## 5. Excel outputs

- Default path pattern: `demo_outputs/field_team_planet_enriched.xlsx` or `demo_outputs/planet_zonal_full_enriched.xlsx`.  
- Sheets commonly include: **`summary`**, **`location_file_s3_map`**, **`daily_enriched`**, optionally **`planet_orders_zonal_stats`**.  

When **Orders zonal fails**, `daily_enriched` still has **HAS_SCENE** metadata rows; **`planet_raw_*`** and **`planet_ndvi_raster`** stay empty until raster delivery works.

---

## 6. S3 / geometry (non-Planet blockers)

- **Chhattisgarh (CG)** KMLs often resolve under `seedworks/kml_files/input_files/CG/CG/`.  
- **Karnataka (KA)** filenames from CSV did **not** resolve under default `KA/KA/` in tests — documentation should say **correct bucket/prefix** must be confirmed with ops.  
- Without a resolved KML polygon, **Planet search cannot run** for that `location_id`.

---

## 7. Suggested wording for external documentation

You can paste or adapt this:

> **PlanetScope (PSScene)** in this product uses Planet’s **Data API** to discover scenes over each field polygon and date range. Scene-level attributes (for example acquisition time and cloud-related metadata) are joined into validation exports.  
>  
> **Raster products** (ortho analytic GeoTIFFs) and **server-side clipping** are delivered through Planet’s **Orders API** and **asset** workflows. Those steps require **appropriate Planet plan permissions** (including, in many cases, explicit **clip** and **download/order** entitlements). **Trial accounts** often support **search** but **deny** clip and/or asset delivery for bundles; behavior must be verified with `scripts/planet_api_permission_audit.py` or Planet support.  
>  
> Vegetation indices from Planet are computed **from band values** using the same formulas as Sentinel-2 in `crop_monitoring.index_calculator` when a GeoTIFF is available locally or after a successful order delivery.

---

## 8. Checklist for “complete” Planet raster story (product / BD)

- [ ] Planet contract includes **Orders** and required **product bundles** (e.g. `analytic_udm2`).  
- [ ] Org has **`clip` tool** if AOI-clipped delivery is required.  
- [ ] Confirm **asset access** for target `PSScene` items (trial may show asset names in search but reject Orders).  
- [ ] S3 KML paths for **all** states in scope.  
- [ ] Operational monitoring: order success rate, quota, runtime.  

---

## 9. How Claude (or a technical writer) should use this file

1. **Start here** for truth on **trial limitations** and **script names**.  
2. Pull **user-facing CSV column meanings** from `PLANET_FIELD_VALIDATION.md`.  
3. Pull **index formulas / NDMI vs SWIR** from `GEOSPATIAL_PLANET_REVIEW.md`.  
4. **Re-run** `planet_api_permission_audit.py` when the customer gets a new Planet tier; paste **new** HTTP summaries into release notes.  

Do **not** copy API keys or database passwords into documentation.

---

*Last consolidated for documentation handoff: aligns with repo behavior under `field_validation/` and `scripts/` as of the Planet trial testing described above.*
