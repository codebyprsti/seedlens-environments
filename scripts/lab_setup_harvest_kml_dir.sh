#!/usr/bin/env bash
# Prepare /home/madanm/kml_files for 253 harvest batch (internal_id.kml + manifest).
#
# Usage:
#   ./scripts/lab_setup_harvest_kml_dir.sh check
#   ./scripts/lab_setup_harvest_kml_dir.sh empty-missing-kmls   # clears ONLY missing_kmls/
#   ./scripts/lab_setup_harvest_kml_dir.sh prepare-target  # creates harvest_all_fields/
#
set -euo pipefail

KML_ROOT="${KML_ROOT:-/home/madanm/kml_files}"
MISSING="${KML_ROOT}/missing_kmls"
HARVEST="${KML_ROOT}/harvest_all_fields"

cmd="${1:-check}"

case "$cmd" in
  check)
    echo "KML_ROOT=$KML_ROOT"
    echo "HARVEST dir: $HARVEST"
    if [[ -d "$HARVEST" ]]; then
      n=$(find "$HARVEST" -maxdepth 1 -name '*.kml' 2>/dev/null | wc -l)
      echo "  .kml count: $n"
      [[ -f "$HARVEST/_harvest_all_manifest.csv" ]] && echo "  manifest: OK" || echo "  manifest: MISSING"
      find "$HARVEST" -maxdepth 1 -name '*.kml' 2>/dev/null | head -3
    else
      echo "  (not created yet — run prepare-target after upload)"
    fi
    echo "missing_kmls: $MISSING"
    if [[ -d "$MISSING" ]]; then
      find "$MISSING" -type f -name '*.kml' 2>/dev/null | wc -l | xargs echo "  .kml files under missing_kmls:"
    fi
    ;;
  empty-missing-kmls)
    if [[ "$MISSING" != "/home/madanm/kml_files/missing_kmls" ]]; then
      echo "REFUSED: path must be /home/madanm/kml_files/missing_kmls"
      exit 3
    fi
    if [[ ! -d "$MISSING" ]]; then
      echo "Nothing to clear: $MISSING does not exist"
      exit 0
    fi
    echo "Will DELETE all files under: $MISSING"
    find "$MISSING" -mindepth 1 | head -10
    echo "..."
    read -r -p "Type yes to delete everything under missing_kmls: " ans
    if [[ "$ans" != "yes" ]]; then
      echo "Aborted."
      exit 0
    fi
    find "$MISSING" -mindepth 1 -delete
    echo "Cleared $MISSING"
    ;;
  prepare-target)
    mkdir -p "$HARVEST"
    echo "Created $HARVEST"
    echo "Upload 253 files from Windows to:"
    echo "  $HARVEST/"
    echo "  (IND-XX-XXXXXX.kml + _harvest_all_manifest.csv)"
    echo ""
    echo "Then:"
    echo "  export SATELLITE_KML_DIR=$HARVEST"
    echo "  export SATELLITE_MAPPING_DIR=$HARVEST"
    echo "  ./scripts/lab_exec.sh validate --expected-kml 253"
    ;;
  *)
    echo "Usage: $0 {check|empty-missing-kmls|prepare-target}"
    exit 2
    ;;
esac
