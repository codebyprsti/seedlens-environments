"""
Insert raw satellite / API payloads into operations.satellite_raw_observation (JSONB).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def json_sanitize_for_jsonb(obj: Any) -> Any:
    """Recursively convert objects to JSON-serializable structures for JSONB."""
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, (np.floating, np.integer)):
        v = float(obj) if isinstance(obj, np.floating) else int(obj)
        return v if np.isfinite(v) or isinstance(obj, np.integer) else None
    if isinstance(obj, np.ndarray):
        return json_sanitize_for_jsonb(obj.tolist())
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): json_sanitize_for_jsonb(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_sanitize_for_jsonb(x) for x in obj]
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj)


def insert_satellite_raw_observation(
    db: Session,
    *,
    location_id: str,
    file_name: str,
    source: str,
    observation_date: Optional[date | str] = None,
    raw_response: Any = None,
    bands: Any = None,
    indices: Any = None,
) -> Optional[int]:
    """
    Insert one raw observation row. Returns new id, or None on failure.
    """
    try:
        with db.begin_nested():
            r = db.execute(
                text("""
                    INSERT INTO operations.satellite_raw_observation
                        (location_id, file_name, source, observation_date, raw_response, bands, indices)
                    VALUES
                        (:location_id, :file_name, :source, :observation_date,
                         CAST(:raw_response AS jsonb), CAST(:bands AS jsonb), CAST(:indices AS jsonb))
                    RETURNING id
                """),
                {
                    "location_id": location_id,
                    "file_name": file_name,
                    "source": source,
                    "observation_date": observation_date,
                    "raw_response": json.dumps(json_sanitize_for_jsonb(raw_response))
                    if raw_response is not None
                    else None,
                    "bands": json.dumps(json_sanitize_for_jsonb(bands)) if bands is not None else None,
                    "indices": json.dumps(json_sanitize_for_jsonb(indices)) if indices is not None else None,
                },
            )
            row = r.fetchone()
            return int(row[0]) if row else None
    except Exception as e:
        logger.exception("insert_satellite_raw_observation failed: %s", e)
        return None
