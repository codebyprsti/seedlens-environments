# Satellite Pipeline v2 — Architecture & Operations

## Overview

Segregates `operations.crop_indices` data into three satellite-specific tables **without modifying** `crop_indices`:

| Table | Mission | Content |
|-------|---------|---------|
| `operations.sentinel2_indices` | S2 L2A | Bands B01–B12, vegetation/moisture/water indices, cloud metadata |
| `operations.sentinel1_indices` | S1 GRD | VV, VH, dB, ratios, RVI |
| `operations.sentinel3_indices` | S3 SLSTR | S7–S9, LST K/C |

Raw payloads land in **`operations.satellite_raw_observation`** (enhanced columns) **before** transformation.

## Layered design

```
Layer 1 — Raw ingestion     store_raw() → satellite_raw_observation (+ run_id, checksum)
Layer 2 — Harmonization     parse Statistical responses → canonical daily rows
Layer 3 — Index calculation API-first (evalscript), compute gaps (CIRE, MNDWI, RVI, LAI…)
Layer 4 — Analytics         optional mirror to crop_indices (SATELLITE_MIRROR_CROP_INDICES=1)
```

## Database setup

```bash
psql $DATABASE_URL -f sql/create_sentinel_indices_tables.sql
psql $DATABASE_URL -f sql/alter_sentinel_indices_harvest_fields.sql   # grower + observation_date uniqueness
psql $DATABASE_URL -f sql/backfill_crop_indices_to_sentinel_tables.sql   # one-time migration
```

### Harvest batch (253 KMLs, no Google geocode)

Maps `IND-XX-NNNNNN.kml` → `internal_id` / `location_id` / grower via Excel/CSV under the code bundle.
Caches mappings in `operations.harvest_field_registry`.

```bash
# Validate mappings + geometry only
python scripts/run_harvest_satellite_batch.py --validate-only

# Full v2 ingestion (reuses raw rows when present — no redundant API)
python scripts/run_harvest_satellite_batch.py \
  --start 2025-12-01 --end 2026-03-18 \
  --season-id RABI_25_26 --batch-strategy quarterly

# Rebuild indices from raw only
python scripts/run_harvest_satellite_batch.py --mode reprocess_raw --start ... --end ...
```

Uniqueness per satellite table (after harvest migration):  
`(location_id, season_id, observation_date, internal_id)` — supports daily time series.

## Migrations

```bash
python scripts/run_sql_migrations_v2.py
psql $DATABASE_URL -f sql/backfill_crop_indices_to_sentinel_tables.sql
```

## Run v2 pipeline

```bash
# Standalone
python scripts/run_satellite_ingestion_v2.py \
  --local-dir "./kml" \
  --start 2025-12-01 --end 2026-03-18 \
  --season-id RABI_25_26 --maxcc 20 --batch-strategy quarterly

# Via copernicus-pipeline CLI (legacy modes unchanged)
copernicus-pipeline run --kml-dir ./kml --start 2025-12-01 --end 2026-03-18 \
  --mode satellite_v2 --season-id RABI_25_26 --batch-strategy quarterly

# Reprocess from raw (no API)
copernicus-pipeline run --kml-dir ./kml --start 2025-12-01 --end 2026-03-18 \
  --mode reprocess_raw --season-id RABI_25_26

# Validate vs legacy crop_indices
python scripts/compare_v2_legacy_indices.py --season-id RABI_25_26
```

Legacy pipeline (`run_crop_analysis_s3_batch.py` / `--mode default`) remains unchanged.

## Implemented (pass 2)

- Checkpoint stages: `satellite_ingestion_checkpoint` wired in orchestrator
- STAC / SH Catalog: `stac_catalog.py` + `operations.stac_scene_catalog`
- S3 S7+S8+S9 in `sentinel_client.fetch_s3_thermal` (evalscript v2)
- S2 full bands Process path: `fetch_s2_bands_full` (B01, B09, …)
- Reprocess: `--mode reprocess_raw` / `reprocess.py`
- Temporal batching: `--batch-strategy weekly|monthly|quarterly|full`
- Dynamic backfill view: `sql/backfill_view_dynamic.sql`
- Gap report per file in orchestrator `counts["gaps"]`

## API strategy

| Satellite | Bulk (season window) | Per-day | Cloud ≤20% |
|-----------|----------------------|---------|------------|
| S2 | 2× Statistical API (SCL-masked v3), P1D + cloud ladder | — | scene ≤60%, pixel mask |
| S1 | — | Process API, 12-day lookback | N/A |
| S3 | — | Process API, ±1 day pad | N/A |

Reprocessing: read `satellite_raw_observation` → harmonize → indices (no API).

## Missing bands added (vs legacy)

- **S2:** B01, B09 (Statistical v2 evalscript); optional B10 via Process path later
- **S3:** S7 (evalscript v2); full SLSTR optional in manifest
- **Indices:** NDRE2, CIRE, MCARI, MNDWI, NDWI_GAO, RVI, cross_pol_ratio

## Checkpoints

- `operations.satellite_ingestion_run` — run metadata
- `operations.satellite_ingestion_checkpoint` — per file/satellite/stage (schema ready; wire in orchestrator as needed)

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `SH_CLIENT_ID` / `SH_CLIENT_SECRET` | required | CDSE OAuth |
| `SKIP_SATELLITE_RAW_OBSERVATION` | off | Skip raw inserts |
| `SATELLITE_MIRROR_CROP_INDICES` | off | Dual-write legacy table |
