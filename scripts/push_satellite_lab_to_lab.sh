#!/usr/bin/env bash
# Push minimum satellite v2 lab bundle to lab machine (run from dev/WSL).
# Usage:
#   export LAB_HOST=madanm@10.8.0.1
#   ./scripts/push_satellite_lab_to_lab.sh
set -euo pipefail

LAB_HOST="${LAB_HOST:-madanm@10.8.0.1}"
LAB_ROOT="${LAB_ROOT:-/home/madanm/SeedIQ-Prod}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Source: $ROOT"
echo "Target: ${LAB_HOST}:${LAB_ROOT}"

ssh "$LAB_HOST" "mkdir -p ${LAB_ROOT}/logs/satellite ${LAB_ROOT}/checkpoints/satellite ${LAB_ROOT}/tmp \
  ${LAB_ROOT}/scripts ${LAB_ROOT}/config ${LAB_ROOT}/satellite_deployment \
  ${LAB_ROOT}/crop_monitoring/satellite_pipeline ${LAB_ROOT}/crop_monitoring/database ${LAB_ROOT}/core"

RSYNC_OPTS=(-avz --progress)

# Core lab entrypoints
for f in run_satellite_lab.py lab_exec.sh lab_continue_harvest.sh lab_validate_harvest_mapping.py \
  lab_safe_clean_kml_dir.sh run_sql_migrations_v2.py run_harvest_satellite_batch.py; do
  rsync "${RSYNC_OPTS[@]}" "$ROOT/scripts/$f" "${LAB_HOST}:${LAB_ROOT}/scripts/"
done
rsync "${RSYNC_OPTS[@]}" "$ROOT/run_pipeline.sh" "$ROOT/deploy_lab.sh" "${LAB_HOST}:${LAB_ROOT}/" 2>/dev/null || true
rsync "${RSYNC_OPTS[@]}" "$ROOT/run_pipeline.sh" "${LAB_HOST}:${LAB_ROOT}/"
rsync "${RSYNC_OPTS[@]}" "$ROOT/satellite_deployment/" "${LAB_HOST}:${LAB_ROOT}/satellite_deployment/"
rsync "${RSYNC_OPTS[@]}" "$ROOT/config/satellite_paths.py" "${LAB_HOST}:${LAB_ROOT}/config/"
rsync "${RSYNC_OPTS[@]}" "$ROOT/crop_monitoring/satellite_pipeline/" "${LAB_HOST}:${LAB_ROOT}/crop_monitoring/satellite_pipeline/"
rsync "${RSYNC_OPTS[@]}" "$ROOT/crop_monitoring/kml_parser.py" "${LAB_HOST}:${LAB_ROOT}/crop_monitoring/" 2>/dev/null || true
rsync "${RSYNC_OPTS[@]}" "$ROOT/crop_monitoring/sh_http_setup.py" "${LAB_HOST}:${LAB_ROOT}/crop_monitoring/" 2>/dev/null || true
rsync "${RSYNC_OPTS[@]}" "$ROOT/crop_monitoring/database/" "${LAB_HOST}:${LAB_ROOT}/crop_monitoring/database/"
rsync "${RSYNC_OPTS[@]}" "$ROOT/core/" "${LAB_HOST}:${LAB_ROOT}/core/"
rsync "${RSYNC_OPTS[@]}" "$ROOT/requirements.txt" "${LAB_HOST}:${LAB_ROOT}/"
rsync "${RSYNC_OPTS[@]}" "$ROOT/docs/LAB_HARVEST_KML_TRANSFER.md" "${LAB_HOST}:${LAB_ROOT}/docs/" 2>/dev/null || true

ssh "$LAB_HOST" "chmod +x ${LAB_ROOT}/scripts/lab_exec.sh ${LAB_ROOT}/scripts/lab_continue_harvest.sh ${LAB_ROOT}/scripts/lab_safe_clean_kml_dir.sh ${LAB_ROOT}/run_pipeline.sh 2>/dev/null; \
  mkdir -p ${LAB_ROOT}/logs/satellite ${LAB_ROOT}/checkpoints/satellite ${LAB_ROOT}/tmp; \
  ls -la ${LAB_ROOT}/scripts/run_satellite_lab.py ${LAB_ROOT}/logs/satellite"

echo "Done. On lab run:"
echo "  cd ${LAB_ROOT} && ./scripts/lab_exec.sh validate --expected-kml 253"
