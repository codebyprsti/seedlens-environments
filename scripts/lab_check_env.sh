#!/usr/bin/env bash
# Quick lab preflight: KML dir, manifest, .env credentials.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KML="${SATELLITE_KML_DIR:-/home/madanm/kml_files}"

echo "=== SeedIQ lab check ==="
echo "Repo: $ROOT"
echo "KML dir: $KML"
echo ""

if [[ -d "$KML" ]]; then
  n=$(find "$KML" -maxdepth 1 -name '*.kml' 2>/dev/null | wc -l)
  echo "KML count: $n"
  ls "$KML"/*.kml 2>/dev/null | head -3 || echo "(no .kml files)"
else
  echo "ERROR: KML dir missing: $KML"
fi

if [[ -f "$KML/_harvest_all_manifest.csv" ]]; then
  echo "Manifest: OK ($KML/_harvest_all_manifest.csv)"
else
  echo "ERROR: Manifest missing: $KML/_harvest_all_manifest.csv"
fi

echo ""
echo "=== Copernicus / DB env ==="
if [[ -f "$ROOT/.env" ]]; then
  echo ".env: $ROOT/.env"
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
else
  echo "ERROR: missing $ROOT/.env — copy from dev machine:"
  echo "  scp user@dev:SeedIQ-Prod/.env $ROOT/.env"
fi
if [[ -n "${SH_CLIENT_ID:-}" ]]; then
  echo "SH_CLIENT_ID: set (${#SH_CLIENT_ID} chars)"
else
  echo "ERROR: SH_CLIENT_ID not set"
fi
if [[ -n "${SH_CLIENT_SECRET:-}" ]]; then
  echo "SH_CLIENT_SECRET: set"
else
  echo "ERROR: SH_CLIENT_SECRET not set"
fi
if [[ -n "${DB_HOST:-}" ]] || [[ -n "${DATABASE_URL:-}" ]]; then
  echo "DB: configured"
else
  echo "WARN: DB_HOST / DATABASE_URL not set"
fi

echo ""
echo "=== Lab scripts ==="
for f in run_satellite_lab.py lab_exec.sh; do
  if [[ -f "$ROOT/scripts/$f" ]]; then
    echo "OK scripts/$f"
  else
    echo "MISSING scripts/$f"
  fi
done

if [[ -d "$ROOT/logs/satellite" ]]; then
  echo "OK logs/satellite/"
else
  echo "WARN: logs/satellite/ missing (lab_exec.sh will create)"
fi

echo ""
echo "=== Copernicus token test (pipeline method) ==="
if [[ -f "$ROOT/scripts/lab_test_copernicus_auth.py" ]]; then
  python "$ROOT/scripts/lab_test_copernicus_auth.py" || echo "Copernicus auth FAILED (fix .env)"
else
  echo "WARN: scripts/lab_test_copernicus_auth.py missing"
fi
