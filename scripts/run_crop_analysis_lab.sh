#!/usr/bin/env bash
# Run KML crop-indices pipeline on lab machine with local files.
# Usage: run in project root on lab (e.g. after SSH madanm@10.8.0.1).
# Ensures: nohup so process survives logout; logs to crop_analysis_lab.log.

set -e
KML_DIR="${KML_DIR:-/home/madanm/kml_files}"
PROJECT_DIR="${PROJECT_DIR:-/home/madanm/SeedIQ-Prod}"
LOG_FILE="${LOG_FILE:-crop_analysis_lab.log}"

cd "$PROJECT_DIR"
if [ -n "${VENV_ACTIVATE}" ]; then
  source "$VENV_ACTIVATE"
elif [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
fi

# Run with nohup so it continues after SSH logout
nohup python scripts/run_crop_analysis_s3_batch.py --local-dir "$KML_DIR" \
  > "$LOG_FILE" 2>&1 &
PID=$!
echo "Started pipeline PID=$PID. Log: $LOG_FILE"
echo "Monitor: tail -f $LOG_FILE"
echo "Stop: kill $PID"
