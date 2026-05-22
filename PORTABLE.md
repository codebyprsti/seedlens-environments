# Standalone Copernicus pipeline CLI (portable folder)

This directory is a **copy of the installable CLI only**. Satellite processing is **not** duplicated here: the CLI runs `scripts/run_crop_analysis_s3_batch.py` from your **full SeedIQ-Prod** (or clone) checkout.

## 1. Keep a full SeedIQ repo somewhere

You need a directory that contains at least:

- `scripts/run_crop_analysis_s3_batch.py`
- `crop_monitoring/`
- `core/` (and the rest of the app your batch imports)

Example: `C:\work\SeedIQ-Prod`

## 2. Point the CLI at that repo

Set **one** of these environment variables to the **absolute** path of that directory:

| Variable | Purpose |
|----------|---------|
| `PIPELINE_REPO_ROOT` | Preferred |
| `SEEDIQ_REPO_ROOT` | Same |
| `COPERNICUS_SEEDIQ_ROOT` | Same |

## 3. Install this folder

```bash
cd path/to/this/folder
pip install -e ".[progress]"
```

You still need **runtime dependencies** for the batch job (same as main app: `numpy`, `sentinelhub`, `sqlalchemy`, `psycopg2-binary`, etc.). Easiest approach:

```bash
pip install -r /path/to/SeedIQ-Prod/requirements.txt
pip install -e ".[progress]"
```

## 4. Run

```powershell
$env:PIPELINE_REPO_ROOT = "C:\work\SeedIQ-Prod"
copernicus-pipeline run --kml-dir "D:\kml" --start 2025-12-01 --end 2026-03-18
```

## Copying this folder from SeedIQ-Prod

From the monorepo root, re-copy after updates:

```powershell
$dest = "C:\path\to\CopernicusPipeline"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Recurse -Force copernicus_pipeline $dest\
Copy-Item -Force pyproject.toml, README.md, Dockerfile.copernicus-pipeline, PORTABLE.md $dest\
New-Item -ItemType Directory -Force -Path "$dest\tests" | Out-Null
Copy-Item -Force tests\test_copernicus_pipeline_dates.py $dest\tests\
Remove-Item -Recurse -Force "$dest\copernicus_pipeline\__pycache__" -ErrorAction SilentlyContinue
```
