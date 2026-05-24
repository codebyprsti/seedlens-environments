# Satellite harvest pipeline — lab deployment

Portable deployment for **253 harvest KMLs** using satellite v2 (`sentinel1/2/3_indices`). Legacy `crop_indices` and `run_crop_analysis_s3_batch.py` are **not** modified.

## Directory layout

```text
SeedIQ-Prod/
├── crop_monitoring/satellite_pipeline/   # v2 pipeline (existing)
├── satellite_deployment/               # lab runner, validate, health
├── config/satellite_paths.py           # portable paths
├── scripts/run_satellite_lab.py        # main lab CLI
├── scripts/run_harvest_satellite_batch.py  # legacy CLI (still works)
├── sql/                                # migrations
├── data/
│   ├── kml/harvest_all_fields/         # 253 renamed KMLs ({internal_id}.kml)
│   └── mapping/                        # Excel, CSV, manifest
├── logs/satellite/                     # ingestion, failed, api, performance
├── checkpoints/satellite/              # health.json, failed_internal_ids.txt
├── deploy_lab.sh / deploy_lab.ps1
├── run_pipeline.sh / run_pipeline.ps1
└── .env
```

## 1. Git workflow (dev machine)

```bash
cd SeedIQ-Prod
git init   # if new repo
git add .
git commit -m "Add satellite v2 lab deployment module"
git remote add origin <your-remote-url>
git push -u origin main
```

**Note:** Do not commit large KML binaries unless intended. Use `data/kml/` on the lab machine (see `.gitignore`).

## 2. Lab machine setup

### Linux

```bash
git clone <your-remote-url> SeedIQ-Prod
cd SeedIQ-Prod
chmod +x deploy_lab.sh run_pipeline.sh
./deploy_lab.sh
cp .env.example .env
# edit .env: DATABASE_URL, SH_CLIENT_ID, SH_CLIENT_SECRET
```

### Windows (PowerShell)

```powershell
git clone <your-remote-url> SeedIQ-Prod
cd SeedIQ-Prod
.\deploy_lab.ps1
Copy-Item .env.example .env
# edit .env
```

## 3. Data preparation

Copy into the repo (not hardcoded Windows paths):

| Source | Destination |
|--------|-------------|
| 253 KML files (`IND-XX-XXXXXX.kml`) | `data/kml/harvest_all_fields/` |
| `_harvest_all_manifest.csv` | `data/mapping/` or `data/kml/harvest_all_fields/` |
| KA / Odisha STRINGbio `.xlsx` | `data/mapping/` |
| `Harvested Field Yields_US24 (1)(Data) (1).csv` | `data/mapping/` |

On-disk KML names are `{internal_id}.kml`. Database **`file_name`** is the canonical name from manifest / `crop_indices` (e.g. `ARELAKAMAPUR ANITHA RAMANNANAVAR USRH24 MANJUNATH.kml`).

## 4. Environment variables

```bash
# Required
DATABASE_URL=postgresql://user:pass@host:5432/dbname
SH_CLIENT_ID=...
SH_CLIENT_SECRET=...

# Optional path overrides (defaults under repo root)
SATELLITE_PROJECT_ROOT=/path/to/SeedIQ-Prod
SATELLITE_KML_DIR=data/kml/harvest_all_fields
SATELLITE_MAPPING_DIR=data/mapping
SATELLITE_LOG_DIR=logs/satellite
SATELLITE_CHECKPOINT_DIR=checkpoints/satellite

# S2 cloud (optional)
S2_MAX_CLOUD_COVER=60
```

## 5. Migrations

```bash
python scripts/run_satellite_lab.py migrate
# or
python scripts/run_sql_migrations_v2.py
```

## 6. Validation (before long run)

```bash
python scripts/run_satellite_lab.py validate
python scripts/run_satellite_lab.py run --validate-only
```

## 7. Full ingestion (253 fields)

```bash
./run_pipeline.sh 2025-12-01 2026-03-18
```

Windows:

```powershell
.\run_pipeline.ps1 -Start 2025-12-01 -End 2026-03-18
```

Direct CLI:

```bash
python scripts/run_satellite_lab.py run \
  --start 2025-12-01 --end 2026-03-18 \
  --season-id RABI_25_26 \
  --batch-strategy quarterly \
  --api-delay 2
```

**Runtime:** expect many hours (API + 108 S1/S3 days per field). Logs: `logs/satellite/ingestion*.log`.

## 8. Resume after interrupt

DB checkpoints (`operations.satellite_ingestion_checkpoint`) + orchestrator `--resume` (default):

```bash
python scripts/run_satellite_lab.py run --start 2025-12-01 --end 2026-03-18
```

Retry **failed fields only** (local list):

```bash
python scripts/run_satellite_lab.py run \
  --start 2025-12-01 --end 2026-03-18 \
  --resume-failed
```

Or:

```bash
RESUME_FAILED=1 ./run_pipeline.sh 2025-12-01 2026-03-18
```

## 9. Reprocess from raw (no API)

```bash
python scripts/run_satellite_lab.py reprocess --start 2025-12-01 --end 2026-03-18
```

## 10. Logs and health

```bash
# Tail main log
tail -f logs/satellite/ingestion*.log

# Failed fields
cat logs/satellite/failed*.log
cat checkpoints/satellite/failed_internal_ids.txt

# Progress / ETA
cat checkpoints/satellite/health.json
```

## 11. Useful maintenance scripts

```bash
# Fix canonical file_name on existing rows
python scripts/backfill_satellite_file_names.py

# Backfill grower_id
python scripts/backfill_satellite_growers.py --report-dir reports/grower

# Purge one field and re-ingest
python scripts/purge_satellite_field_data.py --internal-id IND-KA-600044
python scripts/run_satellite_lab.py run --start 2025-12-01 --end 2026-03-18 --only-internal-id IND-KA-600044 --no-resume
```

## 12. Troubleshooting

| Issue | Action |
|-------|--------|
| KML count ≠ 253 | Copy all files to `data/kml/harvest_all_fields/`; run `validate` |
| Auth failures | Check `SH_CLIENT_ID` / `SH_CLIENT_SECRET`; run `bootstrap` |
| Rate limits | Increase `--api-delay` (default 2s between fields) |
| Wrong `file_name` | Ensure manifest in `data/mapping/`; run `backfill_satellite_file_names.py` |
| NULL `grower_id` | Run `backfill_satellite_growers.py` |
| Resume not skipping | DB checkpoints need same `run_id` or completed COMPLETE stage per file |

## 13. What stays unchanged

- `operations.crop_indices` — not written by v2 harvest path
- `scripts/run_crop_analysis_s3_batch.py` — legacy S3 batch
- `scripts/run_satellite_ingestion_v2.py` — generic local-dir v2 CLI
- Raw-first storage, STAC, quarterly chunking, DB checkpoints

## 14. Single-field test

```bash
python scripts/run_satellite_lab.py run \
  --start 2025-12-01 --end 2026-03-18 \
  --only-internal-id IND-KA-600044 \
  --no-resume
```
