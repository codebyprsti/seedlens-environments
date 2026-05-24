"""
Timing "sync" between Planet acquisition and field validation (not biological SYNC from CSV).

planet_timing_sync_index: 1.0 when acquisition and validation fall on the same calendar day,
then decays with |lag_days| (half at ~14 days gap).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional


def _acq_calendar_date(row: dict[str, Any]) -> Optional[date]:
    raw = row.get("planet_acquisition_datetime_utc") or row.get("planet_best_acquired") or row.get(
        "calendar_date"
    )
    if not raw:
        return None
    s = str(raw).strip()
    try:
        if len(s) >= 10:
            return date.fromisoformat(s[:10])
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def timing_sync_metrics(
    row: dict[str, Any],
    *,
    validation_date: Optional[date],
) -> dict[str, Any]:
    """
    Returns planet_acq_vs_validation_lag_days (validation - acquisition, signed),
    planet_timing_sync_index in (0,1], planet_timing_sync_note.
    """
    acq = _acq_calendar_date(row)
    out: dict[str, Any] = {
        "planet_acq_vs_validation_lag_days": None,
        "planet_timing_sync_index": None,
        "planet_timing_sync_note": (
            "Timing sync: compares Planet acquisition calendar date to field Validation Date "
            "(not biological male/female row sync)."
        ),
    }
    if validation_date is None or acq is None:
        out["planet_timing_sync_note"] = (
            "Cannot compute timing sync: missing validation date or Planet acquisition date."
        )
        return out
    lag = (validation_date - acq).days
    out["planet_acq_vs_validation_lag_days"] = lag
    # Same day = 1.0; symmetric decay (half ~14d)
    out["planet_timing_sync_index"] = float(1.0 / (1.0 + abs(lag) / 14.0))
    return out
