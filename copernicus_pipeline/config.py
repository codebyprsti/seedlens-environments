"""Environment-driven configuration (aliases for legacy SeedIQ settings)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelinePaths:
    """Resolved repository paths (editable install: package lives under repo root)."""

    repo_root: Path

    @property
    def batch_script(self) -> Path:
        return self.repo_root / "scripts" / "run_crop_analysis_s3_batch.py"


def repo_root_from_package() -> Path:
    """
    Root of the SeedIQ application (contains ``scripts/`` and ``crop_monitoring/``).

    When this package is installed or copied **outside** the monorepo, set one of:

    - ``PIPELINE_REPO_ROOT``
    - ``SEEDIQ_REPO_ROOT``
    - ``COPERNICUS_SEEDIQ_ROOT``

    to the absolute path of the SeedIQ-Prod checkout (the directory that contains
    ``scripts/run_crop_analysis_s3_batch.py``).
    """
    for key in ("PIPELINE_REPO_ROOT", "SEEDIQ_REPO_ROOT", "COPERNICUS_SEEDIQ_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        batch = p / "scripts" / "run_crop_analysis_s3_batch.py"
        if p.is_dir() and batch.is_file():
            logger.debug("Using %s=%s as pipeline repo root", key, p)
            return p
        logger.warning(
            "%s=%s is not a valid SeedIQ repo root (expected %s); falling back to package parent",
            key,
            raw,
            batch,
        )
    return Path(__file__).resolve().parent.parent


class PipelineSettings(BaseSettings):
    """Standard env names; values are propagated to what ``core.config`` / batch expect."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_nested_delimiter="__",
    )

    # Optional unified DB URL (mapped to DB_* for the existing app)
    database_url: Optional[str] = None

    # Sentinel (aliases mapped to SH_CLIENT_ID / SH_CLIENT_SECRET in env_sync)
    sentinel_client_id: Optional[str] = None
    sentinel_client_secret: Optional[str] = None

    # Already used by the batch stack
    sh_client_id: Optional[str] = None
    sh_client_secret: Optional[str] = None

    skip_satellite_raw_observation: Optional[str] = None

    # Sentinel-2 cloud (scene ceiling %; pixel mask via SCL in v3 pipeline)
    s2_max_cloud_cover: float = 60.0
    s2_min_valid_pixel_pct: float = 30.0

    log_level: str = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def upper_level(cls, v: object) -> str:
        return str(v or "INFO").upper()


def get_settings() -> PipelineSettings:
    """Instantiate settings (call after ``load_dotenv_files()`` in the CLI)."""
    return PipelineSettings()
