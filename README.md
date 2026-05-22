# Copernicus pipeline CLI (`copernicus-pipeline`)

This repository’s Sentinel Hub / Copernicus field pipeline is implemented in `scripts/run_crop_analysis_s3_batch.py` and `crop_monitoring/`. The **`copernicus_pipeline`** package adds a **production-oriented Typer CLI** that does **not** duplicate satellite logic: it validates inputs, syncs environment variables, configures logging, and spawns the existing batch script.

## Install (editable, recommended)

From the **repository root** (where `crop_monitoring/`, `core/`, and `scripts/` live):

```bash
pip install -r requirements.txt
pip install -e ".[progress]"
```

The console script **`copernicus-pipeline`** is registered by `pyproject.toml`.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Optional `postgresql://user:pass@host:port/db` → mapped to `DB_*` for `core.config` |
| `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASS`, `DB_PORT` | Used if `DATABASE_URL` is not set |
| `SENTINEL_CLIENT_ID` / `SENTINEL_CLIENT_SECRET` | Aliases → `SH_CLIENT_ID` / `SH_CLIENT_SECRET` if those are unset |
| `SH_CLIENT_ID` / `SH_CLIENT_SECRET` | Sentinel Hub (Copernicus Data Space) OAuth |
| `SKIP_SATELLITE_RAW_OBSERVATION` | `1` / `true` / `yes` to skip `satellite_raw_observation` inserts |
| `LOG_LEVEL` | CLI log level (default `INFO`) |

Optional **`.env`** in the current working directory or at the repository root is loaded (non-destructive) via `python-dotenv`.

## Usage

```bash
copernicus-pipeline run \
  --kml-dir /path/to/kmls \
  --start 2025-12-01 \
  --end 2026-03-18 \
  --mode default \
  --season-id RABI_25_26 \
  --limit 100 \
  --only-file one.kml \
  --only-file two.kml \
  --dry-run \
  --log-file logs/run.log \
  --skip-satellite-raw
```

- **`--mode default`**: full pipeline → `operations.crop_indices` (and mirror `field_indices` when enabled in code).
- **`--mode locations_only`**: `operations.field_locations` backfill only (Google geocode when needed).
- **`--jobs N`**: run **N** parallel batch subprocesses, **one KML per process** (higher load on DB and Sentinel; use carefully).
- **`--progress`**: tqdm progress (install optional extra: `pip install -e ".[progress]"`).

Also runnable as:

```bash
python -m copernicus_pipeline run --kml-dir ./kml --start 2025-12-01 --end 2026-03-18
```

## Docker

```bash
docker build -f Dockerfile.copernicus-pipeline -t copernicus-pipeline .
docker run --rm -v /host/kml:/data/kml:ro \
  -e DATABASE_URL -e SH_CLIENT_ID -e SH_CLIENT_SECRET \
  copernicus-pipeline run --kml-dir /data/kml --start 2025-12-01 --end 2026-03-18
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Unexpected error |
| 2 | Validation (bad dates, missing batch script, missing `--kml-dir`, etc.) |
| Non-zero from batch | Propagated subprocess exit code from `run_crop_analysis_s3_batch.py` |

## Legacy wrapper

`scripts/copernicus_kml_pipeline.py` remains as a thin entry that delegates to this package (same repo on `PYTHONPATH` or after `pip install -e .`).

## Copying this CLI outside the monorepo

You can copy **only** this package tree (see `PORTABLE.md` in a standalone export) to another machine or folder. The batch script and satellite code still live in the main SeedIQ repo; set **`PIPELINE_REPO_ROOT`** (or **`SEEDIQ_REPO_ROOT`**) to the **absolute path** of that checkout—the directory that contains `scripts/run_crop_analysis_s3_batch.py` and `crop_monitoring/`.

Example (PowerShell):

```powershell
$env:PIPELINE_REPO_ROOT = "C:\path\to\SeedIQ-Prod"
copernicus-pipeline run --kml-dir "D:\kml" --start 2025-12-01 --end 2026-03-18
```
