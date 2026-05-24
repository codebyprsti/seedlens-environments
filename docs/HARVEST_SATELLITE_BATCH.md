# Harvest-all-fields satellite batch

Parallel v2 path for **253** KML files under `harvest_all_fields/`.

On disk, KMLs may be renamed to `{internal_id}.kml` (e.g. `IND-KA-600044.kml`). The DB **`file_name`** is the canonical name from `_harvest_all_manifest.csv`, STRINGbio Excel, yields CSV, or `crop_indices` (e.g. `ARELAKAMAPUR ANITHA RAMANNANAVAR USRH24 MANJUNATH.kml`). Geometry is read from `kml_path` on disk.

## S1 / S3 vs S2 day counts

| Satellite | Rows per field | Why |
|-----------|----------------|-----|
| **S2** | ~15–25 per season window (example) | Only days with a clear Sentinel-2 L2A scene after cloud/SCL masking (~5-day revisit + weather) |
| **S1 / S3** | **One row per calendar day** in `--start`..`--end` | Process API fetched for every day in the range (values null when no overpass) |

For `2025-12-01` → `2026-03-18` that is **108** S1 and **108** S3 rows per field, while S2 stays at observation days only.

## Constraints preserved

- `operations.crop_indices` is **not** modified.
- Legacy `run_crop_analysis_s3_batch.py` / `--mode default` unchanged.
- **No Google reverse geocode** — `location_id` comes from KML filename / manifest / yields CSV.
- Raw API payloads stored in `operations.satellite_raw_observation` **before** harmonization.
- Existing raw rows are reused (no redundant Statistical API when raw exists).

## Migrations

```bash
python scripts/run_sql_migrations_v2.py
# or
psql $DATABASE_URL -f sql/alter_sentinel_indices_harvest_fields.sql
```

Adds to `sentinel1_indices`, `sentinel2_indices`, `sentinel3_indices`:

- `internal_id`, `grower_name`, `grower_id`, `observation_date`
- `satellite_source`, `cloud_coverage`, `orbit_direction`, `processing_level`
- Unique index: `(location_id, season_id, acquisition_date, satellite_source)` — daily time series safe
- Dropped legacy: `crop_indices_id`, `valid_pixel_fraction` (S2)

Grower linkage uses `operations.growers` (`G_<n>` ids). Excel numeric `Grower ID` is audit-only; names are normalized (trim, lowercase match, fuzzy 85%).

```bash
# Backfill grower_id on existing satellite rows
python scripts/backfill_satellite_growers.py --report-dir reports/grower
```

Registry cache: `operations.harvest_field_registry`

## Mapping sources

Auto-discovered under the code bundle + Downloads:

- `_harvest_all_manifest.csv` (legacy `file_name`)
- `Harvested Field Yields_US24 (1)(Data) (1).csv`
- KA / Odisha STRINGbio Excel workbooks (`Grower ID`, `KML File Name`)

## Lab deployment (recommended)

See **[README_DEPLOYMENT.md](../README_DEPLOYMENT.md)** for portable paths under `data/kml/` and `data/mapping/`.

```bash
./deploy_lab.sh
python scripts/run_satellite_lab.py validate
./run_pipeline.sh 2025-12-01 2026-03-18
```

## Commands (legacy CLI)

```bash
# Validation only (geometry + duplicates + unmatched report)
python scripts/run_harvest_satellite_batch.py --validate-only

# Ingest (quarterly chunking, STAC, checkpoints)
python scripts/run_harvest_satellite_batch.py \
  --start 2025-12-01 --end 2026-03-18 \
  --season-id RABI_25_26

# Reprocess indices from raw (no Copernicus API)
python scripts/run_harvest_satellite_batch.py \
  --mode reprocess_raw --start 2025-12-01 --end 2026-03-18
```

Reports: `reports/harvest/harvest_validation.log`, `harvest_unmatched.csv`

## Resume

- Checkpoint table: `operations.satellite_ingestion_checkpoint`
- Pass `--run-id <uuid>` to continue a partial run
- `--no-resume` forces full re-attempt per file
