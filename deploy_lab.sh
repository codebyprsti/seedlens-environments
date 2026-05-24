#!/usr/bin/env bash
# Bootstrap satellite deployment on Linux lab machine.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
if [[ ! -d .venv ]]; then
  echo "Creating .venv ..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install -q -r requirements.txt
export SATELLITE_PROJECT_ROOT="$ROOT"
"$PY" -m satellite_deployment.bootstrap --install-deps --expected-kml "${EXPECTED_KML:-253}"
echo "Bootstrap complete. Next: cp .env.example .env && edit credentials"
echo "  python scripts/run_satellite_lab.py migrate"
echo "  ./run_pipeline.sh --start 2025-12-01 --end 2026-03-18"
