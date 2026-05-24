#!/usr/bin/env python3
"""Test Copernicus / Sentinel Hub auth the same way the pipeline does."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:
    pass

from crop_monitoring.sh_http_setup import configure_sh_http

configure_sh_http()

cid = os.environ.get("SH_CLIENT_ID", "")
sec = os.environ.get("SH_CLIENT_SECRET", "")
print(f".env path: {_root / '.env'} (exists={(_root / '.env').is_file()})")
print(f"SH_CLIENT_ID: {'set (' + str(len(cid)) + ' chars)' if cid else 'MISSING'}")
print(f"SH_CLIENT_SECRET: {'set' if sec else 'MISSING'}")

if not cid or not sec:
    print("\nFix: create / edit .env on lab with:")
    print("  SH_CLIENT_ID=sh-...")
    print("  SH_CLIENT_SECRET=...")
    sys.exit(2)

from crop_monitoring.statistical_client import _get_config
from sentinelhub import SentinelHubSession

config = _get_config()
try:
    session = SentinelHubSession(config=config)
    token = session.token
    if token:
        print("token OK")
        sys.exit(0)
    print("FAIL: empty token")
    sys.exit(1)
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)
