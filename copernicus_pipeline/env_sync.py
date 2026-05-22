"""Map standard env vars into the names expected by SeedIQ ``core.config`` and Sentinel clients."""

from __future__ import annotations

import logging
import os
from urllib.parse import unquote, urlparse

from copernicus_pipeline.config import PipelineSettings, repo_root_from_package

logger = logging.getLogger(__name__)


def _parse_database_url(url: str) -> dict[str, str]:
    """Split postgresql://user:password@host:port/dbname into DB_* keys."""
    parsed = urlparse(url)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise ValueError(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")
    dbname = (parsed.path or "").lstrip("/")
    if not dbname:
        raise ValueError("DATABASE_URL must include a database name in the path")
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("DATABASE_URL must include a host")
    port = str(parsed.port or "5432")
    return {
        "DB_NAME": dbname,
        "DB_USER": user,
        "DB_PASS": password,
        "DB_HOST": host,
        "DB_PORT": port,
    }


def apply_pipeline_environment(settings: PipelineSettings | None = None) -> None:
    """
    Mutate ``os.environ`` so existing batch code picks up credentials.

    - ``DATABASE_URL`` -> ``DB_NAME``, ``DB_USER``, ``DB_PASS``, ``DB_HOST``, ``DB_PORT`` (if set)
    - ``SENTINEL_CLIENT_ID`` / ``SECRET`` -> ``SH_CLIENT_ID`` / ``SH_CLIENT_SECRET`` when SH_* unset
    """
    s = settings or PipelineSettings()

    if s.database_url and s.database_url.strip():
        try:
            parts = _parse_database_url(s.database_url.strip())
        except ValueError as e:
            logger.warning("Could not parse DATABASE_URL: %s", e)
        else:
            for k, v in parts.items():
                if v and not os.environ.get(k):
                    os.environ[k] = v
                    logger.debug("Set %s from DATABASE_URL", k)

    sid = (s.sentinel_client_id or "").strip()
    sec = (s.sentinel_client_secret or "").strip()
    if sid and not os.environ.get("SH_CLIENT_ID"):
        os.environ["SH_CLIENT_ID"] = sid
        logger.debug("Set SH_CLIENT_ID from SENTINEL_CLIENT_ID")
    if sec and not os.environ.get("SH_CLIENT_SECRET"):
        os.environ["SH_CLIENT_SECRET"] = sec
        logger.debug("Set SH_CLIENT_SECRET from SENTINEL_CLIENT_SECRET")

    # Also allow explicit SH_* from settings model (already in os.environ if set)
    if s.sh_client_id and not os.environ.get("SH_CLIENT_ID"):
        os.environ["SH_CLIENT_ID"] = s.sh_client_id.strip()
    if s.sh_client_secret and not os.environ.get("SH_CLIENT_SECRET"):
        os.environ["SH_CLIENT_SECRET"] = s.sh_client_secret.strip()

    if s.skip_satellite_raw_observation is not None and str(s.skip_satellite_raw_observation).strip():
        v = str(s.skip_satellite_raw_observation).strip()
        if not os.environ.get("SKIP_SATELLITE_RAW_OBSERVATION"):
            os.environ["SKIP_SATELLITE_RAW_OBSERVATION"] = v


def load_dotenv_files() -> None:
    """Load ``.env`` from CWD and from repository root (non-fatal if missing)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(override=False)
    root = repo_root_from_package()
    env_root = root / ".env"
    if env_root.is_file():
        load_dotenv(env_root, override=False)
