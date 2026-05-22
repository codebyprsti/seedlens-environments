"""Sentinel / Copernicus credentials are consumed inside ``crop_monitoring`` (not duplicated here)."""

from __future__ import annotations

from copernicus_pipeline.db import assert_sentinel_configured

__all__ = ["assert_sentinel_configured"]
