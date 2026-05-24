# Lab satellite setup (fix missing run_satellite_lab.py)

If you see:

```text
python: can't open file '.../scripts/run_satellite_lab.py': No such file or directory
logs/satellite/harvest_batch.log: No such file or directory
```

the **satellite v2 lab bundle was never synced** to the lab. Fix below.

---

## Quick fix on lab (after sync from dev)

```bash
cd /home/madanm/SeedIQ-Prod
chmod +x scripts/lab_exec.sh scripts/lab_safe_clean_kml_dir.sh run_pipeline.sh

export SATELLITE_KML_DIR=/home/madanm/kml_files
export SATELLITE_MAPPING_DIR=/home/madanm/kml_files

# Creates logs/satellite, checkpoints, tmp automatically
./scripts/lab_exec.sh validate --expected-kml 253

nohup ./scripts/lab_exec.sh run \
  --start 2025-12-01 --end 2026-03-18 --season-id RABI_25_26 \
  > logs/satellite/harvest_batch.log 2>&1 &

tail -f logs/satellite/harvest_batch.log
```

---

## Sync code from **Windows dev PC** (not from the lab)

The lab has no `/mnt/c/...` path. Run **push** from your Windows machine (WSL or Git Bash):

```bash
# WSL on Windows only:
cd /mnt/c/Users/madan/ENVIRONMENTS/SeedIQ-Prod
export LAB_HOST=madanm@10.8.0.1
./scripts/push_satellite_lab_to_lab.sh
```

Or PowerShell after `git push` → `git pull` on lab.

## Upload KMLs (separate from code sync)

```powershell
# Windows PowerShell
$env:LAB_SSH_PASS = "your-password"
cd C:\Users\madan\ENVIRONMENTS\SeedIQ-Prod
.\scripts\upload_kml_to_lab.ps1 -SampleOnly    # one file test
.\scripts\upload_kml_to_lab.ps1                # all 253
```

Or **git pull** on lab if you committed and pushed:

```bash
cd /home/madanm/SeedIQ-Prod
git pull
```

---

## Verify structure on lab

```bash
cd /home/madanm/SeedIQ-Prod
pwd
ls scripts/run_satellite_lab.py scripts/lab_exec.sh
ls -d logs/satellite checkpoints tmp satellite_deployment crop_monitoring/satellite_pipeline
find . -name "*satellite*" | head -20
```

---

## Monitoring

```bash
ps -ef | grep run_satellite_lab
tail -f logs/satellite/harvest_batch.log
tail -f logs/satellite/validation.log
cat checkpoints/satellite/failed_internal_ids.txt 2>/dev/null
```

```sql
SELECT COUNT(*) FROM operations.sentinel2_indices;
SELECT internal_id, file_name, grower_name, grower_id
FROM operations.sentinel2_indices
WHERE internal_id = 'IND-KA-600044' LIMIT 5;
```

---

## Commands reference

| Action | Command |
|--------|---------|
| Validate | `./scripts/lab_exec.sh validate --expected-kml 253` |
| Migrate | `./scripts/lab_exec.sh migrate` |
| Run batch | `./scripts/lab_exec.sh run --start 2025-12-01 --end 2026-03-18` |
| One field | `./scripts/lab_exec.sh run ... --only-internal-id IND-KA-600044` |
| Reprocess raw | `./scripts/lab_exec.sh reprocess --start ... --end ...` |

Log files under `logs/satellite/`: `validation.log`, `ingestion*.log`, `failed*.log`, `harvest_batch.log` (nohup stdout).
