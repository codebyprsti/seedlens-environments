#!/usr/bin/env bash
# Resume harvest satellite batch on lab (same run_id, skip COMPLETE, max cloud 20).
# Run on lab after syncing latest scripts from dev (see push_satellite_lab_to_lab.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export SATELLITE_KML_DIR="${SATELLITE_KML_DIR:-/home/madanm/kml_files/harvest_all_fields}"
export SATELLITE_MAPPING_DIR="${SATELLITE_MAPPING_DIR:-/home/madanm/kml_files/harvest_all_fields}"
export S2_MAX_CLOUD_COVER="${S2_MAX_CLOUD_COVER:-20}"
export S2_CLOUD_FALLBACK_LADDER="${S2_CLOUD_FALLBACK_LADDER:-20}"
# Fewer parallel S1/S3 Process API calls → fewer SHRateLimitWarning (default was 8)
export SATELLITE_MAX_S1_S3_WORKERS="${SATELLITE_MAX_S1_S3_WORKERS:-3}"
API_DELAY="${SATELLITE_API_DELAY:-6}"
# Daily S1/S3: keep calendar-day fetch (108/field). Do NOT use --s1-s3-on-s2-days-only unless
# you only need SAR/thermal on clear S2 days (~20/field).
S1_S3_FLAG=()

START="${1:-2025-12-01}"
END="${2:-2026-03-18}"
SEASON="${3:-RABI_25_26}"
STATE="$ROOT/checkpoints/satellite/run_state.json"

if [[ ! -f "$STATE" ]]; then
  echo "ERROR: missing $STATE — run a batch first or set --run-id manually" >&2
  exit 2
fi

RUN_ID="$(python3 -c "import json; print(json.load(open('$STATE'))['run_id'])")"
echo "run_id from state: $RUN_ID"

python3 <<'PY' || true
import json, uuid
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(".env")
from sqlalchemy import text
from core.db import SessionLocal

state = json.loads(Path("checkpoints/satellite/run_state.json").read_text())
rid = str(state["run_id"])
db = SessionLocal()
rows = db.execute(
    text("""
        SELECT COUNT(*) FROM operations.satellite_ingestion_checkpoint
        WHERE run_id = CAST(:rid AS uuid) AND stage = 'complete' AND status = 'done'
    """),
    {"rid": rid},
).scalar()
print(f"complete checkpoints (this run_id): {rows}")
db.close()
PY

EXTRA=()
if python3 "$ROOT/scripts/run_satellite_lab.py" run -h 2>&1 | grep -q continue-last-run; then
  EXTRA+=(--continue-last-run)
  echo "Using --continue-last-run"
else
  EXTRA+=(--run-id "$RUN_ID")
  echo "Lab CLI old — using --run-id $RUN_ID (orchestrator skips COMPLETE per file)"
fi

exec "$ROOT/scripts/lab_exec.sh" run \
  "${EXTRA[@]}" \
  "${S1_S3_FLAG[@]}" \
  --maxcc 20 \
  --api-delay "$API_DELAY" \
  --start "$START" \
  --end "$END" \
  --season-id "$SEASON"
