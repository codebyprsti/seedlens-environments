#!/usr/bin/env bash
# Run full harvest satellite ingestion (253 KMLs). Resumable via DB checkpoints + --resume-failed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export SATELLITE_PROJECT_ROOT="$ROOT"

if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

START="${1:-2025-12-01}"
END="${2:-2026-03-18}"
shift 2 2>/dev/null || true

EXTRA=()
if [[ "${RESUME_FAILED:-0}" == "1" ]]; then
  EXTRA+=(--resume-failed)
fi

python scripts/run_satellite_lab.py run \
  --start "$START" \
  --end "$END" \
  --season-id "${SEASON_ID:-RABI_25_26}" \
  --batch-strategy "${BATCH_STRATEGY:-quarterly}" \
  --api-delay "${API_DELAY:-2}" \
  "${EXTRA[@]}" \
  "$@"
