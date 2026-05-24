#!/usr/bin/env bash
# Lab execution wrapper: ensures dirs + venv, then runs run_satellite_lab.py
# Usage:
#   ./scripts/lab_exec.sh validate --expected-kml 253
#   ./scripts/lab_exec.sh run --start 2025-12-01 --end 2026-03-18 --season-id RABI_25_26 --maxcc 20
#   ./scripts/lab_exec.sh run --continue-last-run --start ... --end ... --maxcc 20   # after partial batch
#   nohup ./scripts/lab_exec.sh run ... > logs/satellite/harvest_batch.log 2>&1 &
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export SATELLITE_PROJECT_ROOT="$ROOT"

# Default lab paths (override in ~/.bashrc or before calling)
export SATELLITE_KML_DIR="${SATELLITE_KML_DIR:-/home/madanm/kml_files}"
export SATELLITE_MAPPING_DIR="${SATELLITE_MAPPING_DIR:-/home/madanm/kml_files}"
export SATELLITE_LOG_DIR="${SATELLITE_LOG_DIR:-$ROOT/logs/satellite}"
export SATELLITE_CHECKPOINT_DIR="${SATELLITE_CHECKPOINT_DIR:-$ROOT/checkpoints/satellite}"

if [[ ! -f "$ROOT/scripts/run_satellite_lab.py" ]]; then
  echo "ERROR: missing $ROOT/scripts/run_satellite_lab.py" >&2
  echo "Sync satellite lab bundle from dev machine (see scripts/push_satellite_lab_to_lab.sh)" >&2
  exit 2
fi

mkdir -p "$ROOT/logs/satellite" "$ROOT/checkpoints" "$ROOT/checkpoints/satellite" "$ROOT/tmp"

if [[ -d "$ROOT/venv/bin" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/venv/bin/activate"
elif [[ -d "$ROOT/.venv/bin" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
else
  echo "WARN: no venv found at $ROOT/venv or $ROOT/.venv" >&2
fi

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
else
  echo "WARN: $ROOT/.env not found — SH_CLIENT_ID/SECRET and DB_* may be unset" >&2
fi

# sentinelhub reads SHConfig; pipeline uses SH_CLIENT_ID / SH_CLIENT_SECRET from env
export SH_CLIENT_ID="${SH_CLIENT_ID:-}"
export SH_CLIENT_SECRET="${SH_CLIENT_SECRET:-}"

exec python "$ROOT/scripts/run_satellite_lab.py" "$@"
