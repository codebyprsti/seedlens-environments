# Lab harvest KML transfer & mapping validation (253 files)

**Do not upload all 253 files until one sample file passes mapping + ingestion + DB checks.**

## Mapping flow (validated in code)

```text
On-disk KML:  IND-KA-600044.kml          (internal_id = filename)
       ↓
_harvest_all_manifest.csv + STRINGbio Excel + yields CSV + crop_indices
       ↓
canonical file_name:  ARELAKAMAPUR ANITHA RAMANNANAVAR USRH24 MANJUNATH.kml
       ↓
location_id:          IND-KA-600044  (= internal_id)
       ↓
grower_name / grower_id from STRINGbio + operations.growers
       ↓
Satellite tables:     file_name = canonical, internal_id column = IND-KA-600044
                      legacy_file_name = IND-KA-600044.kml (disk)
```

Implementation: `crop_monitoring/satellite_pipeline/harvest_field_mapping.py`  
Ingestion: `scripts/run_satellite_lab.py` → `satellite_deployment/runner.py`

---

## Paths

| Role | Path |
|------|------|
| Local source | `C:\Users\madan\Downloads\code-20260512T095809Z-3-001\code\planetscope-data-apis\kml\harvest_all_fields\` |
| Lab KML dir | `/home/madanm/kml_files/` |
| Lab repo | `/home/madanm/SeedIQ-Prod` |

Copy **manifest + Excel/CSV** with KMLs (manifest is already inside `harvest_all_fields`).

---

## STEP 1 — Validate mapping locally (before any upload)

```powershell
cd C:\Users\madan\ENVIRONMENTS\SeedIQ-Prod

$SRC = "C:\Users\madan\Downloads\code-20260512T095809Z-3-001\code\planetscope-data-apis\kml\harvest_all_fields"

# Full mapping report (253 files)
python scripts/lab_validate_harvest_mapping.py `
  --kml-dir $SRC `
  --mapping-dir $SRC `
  --resolve-growers `
  --log-level INFO

# Single sample (recommended first)
python scripts/lab_validate_harvest_mapping.py `
  --kml-dir $SRC `
  --mapping-dir $SRC `
  --only-internal-id IND-KA-600044 `
  --resolve-growers
```

**Expect for sample `IND-KA-600044`:**
- `disk` = `IND-KA-600044.kml`
- `file_name` = `ARELAKAMAPUR ANITHA RAMANNANAVAR USRH24 MANJUNATH.kml` (from manifest, **not** internal_id)
- `grower_name` populated; `grower_id` = `G_*` after `--resolve-growers`

Reports: `reports/harvest/harvest_validation.log`, `harvest_unmatched.csv`

---

## STEP 2 — Test ONE file on lab

### 2a. SSH

```bash
ssh madanm@10.8.0.1
```

### 2b. Upload one KML + manifest (from Windows PowerShell)

```powershell
$SRC = "C:\Users\madan\Downloads\code-20260512T095809Z-3-001\code\planetscope-data-apis\kml\harvest_all_fields"
$LAB = "madanm@10.8.0.1:/home/madanm/kml_files"

# Create remote dir
ssh madanm@10.8.0.1 "mkdir -p /home/madanm/kml_files"

# One sample + manifest (required for file_name resolution)
scp "$SRC\IND-KA-600044.kml" "${LAB}/"
scp "$SRC\_harvest_all_manifest.csv" "${LAB}/"
```

### 2c. Verify on lab

```bash
ls -la /home/madanm/kml_files/
# Expect: IND-KA-600044.kml, _harvest_all_manifest.csv
```

### 2d. Lab env + validate mapping

```bash
cd /home/madanm/SeedIQ-Prod
source venv/bin/activate   # or: python3 -m venv venv && source venv/bin/activate

export SATELLITE_KML_DIR=/home/madanm/kml_files
export SATELLITE_MAPPING_DIR=/home/madanm/kml_files

python scripts/lab_validate_harvest_mapping.py \
  --kml-dir /home/madanm/kml_files \
  --mapping-dir /home/madanm/kml_files \
  --only-internal-id IND-KA-600044 \
  --resolve-growers
```

### 2e. Migrate + single-file ingestion

```bash
python scripts/run_satellite_lab.py migrate

nohup python scripts/run_satellite_lab.py run \
  --start 2025-12-01 --end 2026-03-18 \
  --season-id RABI_25_26 \
  --only-internal-id IND-KA-600044 \
  --log-level INFO \
  > /home/madanm/SeedIQ-Prod/logs/satellite/single_field_test.log 2>&1 &
echo $!
```

### 2f. Tail logs

```bash
tail -f /home/madanm/SeedIQ-Prod/logs/satellite/single_field_test.log
tail -f /home/madanm/SeedIQ-Prod/logs/satellite/ingestion.log
```

### 2g. DB validation SQL (sample `IND-KA-600044`)

```sql
-- Registry: canonical file_name vs disk
SELECT internal_id, location_id, file_name, legacy_file_name, grower_name, grower_id
FROM operations.harvest_field_registry
WHERE internal_id = 'IND-KA-600044';

-- S2 indices: must use business file_name, not internal_id.kml
SELECT location_id, internal_id, file_name, legacy_file_name, grower_name, grower_id,
       acquisition_date, ndvi, cloud_coverage
FROM operations.sentinel2_indices
WHERE internal_id = 'IND-KA-600044'
ORDER BY acquisition_date
LIMIT 10;

-- Row counts per satellite
SELECT 'S1' AS sat, COUNT(*) FROM operations.sentinel1_indices WHERE internal_id = 'IND-KA-600044'
UNION ALL
SELECT 'S2', COUNT(*) FROM operations.sentinel2_indices WHERE internal_id = 'IND-KA-600044'
UNION ALL
SELECT 'S3', COUNT(*) FROM operations.sentinel3_indices WHERE internal_id = 'IND-KA-600044';

-- Raw observations persisted
SELECT satellite_source, COUNT(*) 
FROM operations.satellite_raw_observation
WHERE file_name LIKE '%ANITHA RAMANNANAVAR%MANJUNATH%'
   OR location_id = 'IND-KA-600044'
GROUP BY satellite_source;

-- STAC scenes linked
SELECT COUNT(*) FROM operations.stac_scene_catalog
WHERE location_id = 'IND-KA-600044';

-- Sanity: file_name must NOT equal internal_id.kml
SELECT COUNT(*) AS bad_names
FROM operations.sentinel2_indices
WHERE internal_id = 'IND-KA-600044'
  AND file_name = 'IND-KA-600044.kml';
-- bad_names should be 0
```

---

## STEP 3 — Safe clean lab KML folder (only after failed test or before full upload)

```bash
chmod +x /home/madanm/SeedIQ-Prod/scripts/lab_safe_clean_kml_dir.sh
/home/madanm/SeedIQ-Prod/scripts/lab_safe_clean_kml_dir.sh /home/madanm/kml_files
# Type: yes
```

**Manual alternative (same safety — path must match exactly):**

```bash
TARGET=/home/madanm/kml_files
test "$TARGET" = "/home/madanm/kml_files" || { echo "refused"; exit 1; }
find "$TARGET" -maxdepth 1 -type f -name '*.kml' -delete
rm -f "$TARGET/_harvest_all_manifest.csv"
ls "$TARGET" | wc -l
```

---

## STEP 4 — Upload all 253 files (after single-file pass)

### rsync (preferred, from Windows WSL or Git Bash)

```bash
SRC="/mnt/c/Users/madan/Downloads/code-20260512T095809Z-3-001/code/planetscope-data-apis/kml/harvest_all_fields"
DEST="madanm@10.8.0.1:/home/madanm/kml_files/"

rsync -avz --progress --partial \
  --include='*.kml' --include='_harvest_all_manifest.csv' --exclude='*' \
  "$SRC/" "$DEST"
```

### scp fallback (PowerShell — slower)

```powershell
$SRC = "C:\Users\madan\Downloads\code-20260512T095809Z-3-001\code\planetscope-data-apis\kml\harvest_all_fields"
scp -r "$SRC\*.kml" "$SRC\_harvest_all_manifest.csv" madanm@10.8.0.1:/home/madanm/kml_files/
```

### Verify count on lab

```bash
ssh madanm@10.8.0.1 "find /home/madanm/kml_files -maxdepth 1 -name '*.kml' | wc -l"
# Expected: 253
test -f /home/madanm/kml_files/_harvest_all_manifest.csv && echo "manifest OK"
```

---

## STEP 5 — Full batch on lab

```bash
cd /home/madanm/SeedIQ-Prod
source venv/bin/activate
export SATELLITE_KML_DIR=/home/madanm/kml_files
export SATELLITE_MAPPING_DIR=/home/madanm/kml_files

# Pre-flight (all 253)
python scripts/run_satellite_lab.py validate --expected-kml 253
python scripts/lab_validate_harvest_mapping.py \
  --kml-dir /home/madanm/kml_files \
  --mapping-dir /home/madanm/kml_files \
  --resolve-growers

# Full run (resumable)
nohup python scripts/run_satellite_lab.py run \
  --start 2025-12-01 --end 2026-03-18 \
  --season-id RABI_25_26 \
  --expected-kml 253 \
  > logs/satellite/harvest_batch.log 2>&1 &
echo $!

tail -f logs/satellite/harvest_batch.log
```

Resume failed only:

```bash
python scripts/run_satellite_lab.py run --resume-failed \
  --start 2025-12-01 --end 2026-03-18 --season-id RABI_25_26
```

---

## Safety checklist

| Check | How |
|-------|-----|
| No delete outside `/home/madanm/kml_files` | `lab_safe_clean_kml_dir.sh` hard-coded path + confirmation |
| No duplicate KML basenames | `INTERNAL_ID_RE` + duplicate check in mapping |
| Original `file_name` in DB | `file_name` ≠ `IND-*.kml` in sentinel tables (SQL above) |
| `internal_id` traceable | `internal_id` column + `legacy_file_name` on disk name |
| NULL `grower_id` | `harvest_unmatched.csv` + grower resolver warnings |
| Resumable | `operations.satellite_ingestion_checkpoint`, `--resume-failed` |

---

## Quick reference commands

| Step | Command |
|------|---------|
| 1 SSH | `ssh madanm@10.8.0.1` |
| 2 One file upload | `scp ... IND-KA-600044.kml ... _harvest_all_manifest.csv` |
| 3 Verify upload | `ls /home/madanm/kml_files/` |
| 4 Mapping test | `python scripts/lab_validate_harvest_mapping.py --only-internal-id IND-KA-600044 ...` |
| 5 Single ingest | `python scripts/run_satellite_lab.py run --only-internal-id IND-KA-600044 ...` |
| 6 DB check | SQL in §2g |
| 7 Safe clean | `lab_safe_clean_kml_dir.sh /home/madanm/kml_files` |
| 8 Upload 253 | `rsync -avz ...` |
| 9 Verify 253 | `find ... -name '*.kml' \| wc -l` |
| 10 Full batch | `python scripts/run_satellite_lab.py run ...` |
| 11 Tail logs | `tail -f logs/satellite/harvest_batch.log` |
