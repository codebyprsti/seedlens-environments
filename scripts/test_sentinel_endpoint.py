#!/usr/bin/env python3
"""
Minimal FastAPI app with only the Sentinel router to test GET /sentinel/layer.
Run: python scripts/test_sentinel_endpoint.py
Then: GET http://127.0.0.1:8020/sentinel/layer?layer_name=ndvi&min_lon=78.48&min_lat=17.51&max_lon=78.50&max_lat=17.53&date_from=2026-02-01
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not os.environ.get("SH_CLIENT_ID") or not os.environ.get("SH_CLIENT_SECRET"):
    raise RuntimeError(
        "Missing SH_CLIENT_ID/SH_CLIENT_SECRET. Set them in your local .env (gitignored) before running."
    )

from fastapi import FastAPI
from api.v1.endpoints import sentinel

app = FastAPI(title="Sentinel test")
app.include_router(sentinel.router, prefix="/sentinel", tags=["Sentinel"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8020)
