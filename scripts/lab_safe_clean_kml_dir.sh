#!/usr/bin/env bash
# Safely remove ONLY *.kml and _harvest_all_manifest.csv under a fixed directory.
# Usage: ./scripts/lab_safe_clean_kml_dir.sh /home/madanm/kml_files
set -euo pipefail

TARGET="${1:-}"

if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 /home/madanm/kml_files" >&2
  exit 2
fi

# Resolve to absolute path
TARGET="$(cd "$TARGET" && pwd)"

# Safety: must be exactly the lab KML folder (adjust if your path differs)
ALLOWED="/home/madanm/kml_files"
if [[ "$TARGET" != "$ALLOWED" ]]; then
  echo "REFUSED: target must be exactly $ALLOWED (got $TARGET)" >&2
  exit 3
fi

if [[ ! -d "$TARGET" ]]; then
  echo "Directory does not exist: $TARGET" >&2
  exit 4
fi

echo "Target: $TARGET"
echo "Contents before delete:"
find "$TARGET" -maxdepth 1 -type f \( -name '*.kml' -o -name '_harvest_all_manifest.csv' \) | wc -l

read -r -p "Delete all .kml and manifest in $TARGET? [yes/N] " ans
if [[ "$ans" != "yes" ]]; then
  echo "Aborted."
  exit 0
fi

find "$TARGET" -maxdepth 1 -type f -name '*.kml' -delete
rm -f "$TARGET/_harvest_all_manifest.csv"
echo "Done. Remaining files in folder:"
ls -la "$TARGET" | head -20
