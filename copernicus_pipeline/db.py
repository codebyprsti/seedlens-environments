"""Optional checks for database and Sentinel configuration before spawning the batch."""

from __future__ import annotations

import os

from copernicus_pipeline.errors import ValidationError


def assert_database_configured() -> None:
    """Require either DATABASE_URL (after env_sync) or DB_HOST + DB_NAME."""
    if (os.environ.get("DATABASE_URL") or "").strip():
        return
    if (os.environ.get("DB_HOST") or "").strip() and (os.environ.get("DB_NAME") or "").strip():
        return
    raise ValidationError(
        "Database not configured: set DATABASE_URL or DB_HOST/DB_NAME (and DB_USER/DB_PASS) in the environment or .env"
    )


def assert_sentinel_configured() -> None:
    """Require Sentinel Hub OAuth client credentials (CDSE / Copernicus)."""
    cid = (os.environ.get("SH_CLIENT_ID") or "").strip()
    sec = (os.environ.get("SH_CLIENT_SECRET") or "").strip()
    if cid and sec:
        return
    raise ValidationError(
        "Sentinel Hub not configured: set SH_CLIENT_ID and SH_CLIENT_SECRET "
        "(or SENTINEL_CLIENT_ID and SENTINEL_CLIENT_SECRET) in the environment or .env"
    )
