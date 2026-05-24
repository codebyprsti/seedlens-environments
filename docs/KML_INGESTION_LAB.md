# KML Ingestion Pipeline — Remote Lab Machine

Run the crop-indices pipeline on a remote lab machine (~9376 KML files, resumable, logout-safe).

---

## Step 1 — Copy KML Files to Lab Machine

**Option A: From S3 (recommended if files are in S3)**  
On the **lab machine** (after SSH), run:

```bash
aws s3 sync s3://prsti-public-data/seedworks/kml_files/input_files/ /home/madanm/kml_files/
```

Or from your **local machine** with SCP (if you have a local copy):

```bash
scp -r /local/path/kml_files madanm@10.8.0.1:/home/madanm/kml_files
```

**Option B: Only a specific prefix (e.g. Odisha/bALASORE)**

```bash
aws s3 sync s3://prsti-public-data/seedworks/kml_files/input_files/Odisha/ /home/madanm/kml_files/Odisha/
```

---

## Step 2 — SSH into Lab Machine

```bash
ssh madanm@10.8.0.1
```

---

## Step 3 — Setup Python Environment on Lab

Clone/copy the project if not already there, then:

```bash
cd /home/madanm/SeedIQ-Prod   # or your project path
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Configure environment (DB and Sentinel Hub):

- Copy `.env` from your dev machine or set at least:
  - `DATABASE_URL` (or whatever `core.db` uses)
  - `SH_CLIENT_ID`, `SH_CLIENT_SECRET` (Sentinel Hub / Copernicus)

---

## Step 4 — Run Pipeline in Background (survives logout)

Use **nohup** so the script keeps running after you log out:

```bash
cd /home/madanm/SeedIQ-Prod
source venv/bin/activate

nohup python scripts/run_crop_analysis_s3_batch.py --local-dir /home/madanm/kml_files \
  > crop_analysis_lab.log 2>&1 &
echo $!
```

- `nohup` — process continues after SSH logout  
- `> crop_analysis_lab.log 2>&1` — stdout and stderr go to the log file  
- `&` — run in background  
- Save the printed PID so you can stop the process if needed  

Or use the helper script:

```bash
chmod +x scripts/run_crop_analysis_lab.sh
./scripts/run_crop_analysis_lab.sh
```

---

## Step 5 — Get Process ID (if you didn’t save it)

```bash
ps aux | grep run_crop_analysis_s3_batch
```

---

## Step 6 — Monitor Logs

```bash
tail -f crop_analysis_lab.log
```

You’ll see lines like:

- `Skipping file (already processed for season)` — resume working  
- `Inserted row for date: ...` — progress  
- `Failed: ...` — single-file failure (pipeline continues to next file)

---

## Step 7 — Stop the Process (if needed)

```bash
kill -9 <PID>
```

After a stop or crash, re-run the same command; already-processed files are skipped (resume logic below).

---

## Step 8 — Resume Logic (built in)

Before processing each file, the script checks:

```sql
SELECT 1 FROM operations.crop_indices
WHERE file_name = :file_name AND season_id = 'RABI_25_26'
LIMIT 1;
```

- If a row exists → **skip** that file (no duplicate work).  
- So you can **stop and re-run** anytime; only remaining files are processed.

---

## Step 9 — Local File Reading (no S3 in loop)

With `--local-dir`:

- KML files are read from disk (e.g. `/home/madanm/kml_files/`).
- No S3 calls during the loop → faster and no AWS dependency for file access.
- Recursive: all `*.kml` under the directory are listed and processed.

---

## Step 10 — Optional: Limit or Dry Run

- Process only first N files:
  ```bash
  python scripts/run_crop_analysis_s3_batch.py --local-dir /home/madanm/kml_files --limit 100
  ```
- List files without processing:
  ```bash
  python scripts/run_crop_analysis_s3_batch.py --local-dir /home/madanm/kml_files --dry-run
  ```

---

## Step 11 — Logging

The script logs:

- **Processing:** file name, village, grower, area, etc.  
- **Skipping existing file** when already processed for the season.  
- **Inserted successfully** (and row counts).  
- **Failed** for a file (with error); pipeline continues to the next file.

---

## Step 12 — Expected Outcome

- Script runs in background and **continues after SSH logout** (nohup).  
- **Resumable:** re-run the same command to process only remaining files.  
- **No re-download** of KMLs when using `--local-dir`.  
- **Single-file failure** does not stop the run; failed files can be retried on next run (they are not marked as processed).  
- ~9376 files: runtime depends on API and DB; local KML access reduces I/O time.

**Note:** The pipeline uses one process with parallel API calls (S2 + S3 + S1) per file and batch commit per file. Multi-worker (e.g. `multiprocessing.Pool`) is not enabled by default to avoid DB connection and Sentinel API rate-limit issues; it can be added later with per-worker DB sessions and throttling.

---

## Summary Commands (copy-paste on lab)

```bash
# 1) Sync KMLs from S3 (once)
aws s3 sync s3://prsti-public-data/seedworks/kml_files/input_files/ /home/madanm/kml_files/

# 2) SSH
ssh madanm@10.8.0.1

# 3) Env (once)
cd /home/madanm/SeedIQ-Prod && source venv/bin/activate

# 4) Run (logout-safe)
nohup python scripts/run_crop_analysis_s3_batch.py --local-dir /home/madanm/kml_files > crop_analysis_lab.log 2>&1 &
echo $!

# 5) Monitor
tail -f crop_analysis_lab.log
```
