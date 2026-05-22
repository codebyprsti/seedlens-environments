"""
Structured logging of pipeline run metadata for audit and debugging.

Aligns with document: "Record Mean Value", "Export Data" — trace which runs
produced which results.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("crop_monitoring.run")


def log_run(
    kml_path: str,
    start_date: str,
    end_date: str,
    maxcc_used: float,
    *,
    valid_pixels: Optional[int] = None,
    indices_computed: Optional[list[str]] = None,
    success: bool = True,
    error: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Log one pipeline run for audit trail."""
    msg = (
        f"run kml={kml_path} start={start_date} end={end_date} maxcc={maxcc_used} "
        f"valid_pixels={valid_pixels} indices={indices_computed} success={success}"
    )
    if error:
        msg += f" error={error}"
    logger.info(msg, extra=extra or {})
