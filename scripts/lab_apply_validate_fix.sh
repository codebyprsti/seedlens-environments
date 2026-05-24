#!/usr/bin/env bash
# Patch satellite_deployment/validate.py on lab to use _get_config() (fixes invalid_client when .env is OK).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VF="$ROOT/satellite_deployment/validate.py"
if [[ ! -f "$VF" ]]; then
  echo "Missing $VF"
  exit 2
fi
python3 << 'PY'
from pathlib import Path
p = Path("satellite_deployment/validate.py")
text = p.read_text(encoding="utf-8")
old = """        from sentinelhub import SentinelHubSession

        session = SentinelHubSession()
        token = session.token"""
new = """        from sentinelhub import SentinelHubSession

        from crop_monitoring.statistical_client import _get_config

        config = _get_config()
        if not (config.sh_client_id or "").strip() or not (config.sh_client_secret or "").strip():
            r.fail(
                "SH_CLIENT_ID / SH_CLIENT_SECRET not set in .env"
            )
            return r
        session = SentinelHubSession(config=config)
        token = session.token"""
if old not in text:
    if "SentinelHubSession(config=config)" in text:
        print("Already patched:", p)
    else:
        print("Could not find block to patch in", p)
        raise SystemExit(1)
else:
    p.write_text(text.replace(old, new), encoding="utf-8")
    print("Patched", p)
PY

echo "Test:"
cd "$ROOT"
source venv/bin/activate 2>/dev/null || true
set -a && source .env && set +a
python scripts/run_satellite_lab.py validate --expected-kml 0 --skip-db-test 2>&1 | tail -5 || true
