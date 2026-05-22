"""
Reverse geocoding: get administrative location (village, district, state, country, etc.)
from polygon centroid using OpenStreetMap Nominatim.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
# Nominatim requires a valid User-Agent; use app name
USER_AGENT = "SeedIQ-CropMonitoring/1.0"
# Rate limit: 1 request per second for Nominatim usage policy
NOMINATIM_DELAY_SEC = 1.0
# On 429, wait this long then retry once
NOMINATIM_RETRY_DELAY_SEC = 5.0


def get_location_from_coordinates(lat: float, lon: float) -> dict[str, Optional[str]]:
    """
    Reverse geocode (lat, lon) using OSM Nominatim.
    Returns village, town, district, state, country, postcode.
    Returns empty strings for missing fields. On API failure returns empty dict values.
    """
    out: dict[str, Optional[str]] = {
        "village": None,
        "town": None,
        "district": None,
        "state": None,
        "country": None,
        "postcode": None,
    }
    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            time.sleep(NOMINATIM_DELAY_SEC)
            resp = requests.get(
                NOMINATIM_URL,
                params={"lat": lat, "lon": lon, "format": "json"},
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
            if resp.status_code == 429 and attempt == 0:
                logger.warning("Nominatim rate limit (429); waiting %.0fs then retry.", NOMINATIM_RETRY_DELAY_SEC)
                time.sleep(NOMINATIM_RETRY_DELAY_SEC)
                continue
            resp.raise_for_status()
            data = resp.json()
            last_err = None
            break
        except Exception as e:
            last_err = e
            if attempt == 0 and getattr(e, "response", None) and getattr(e.response, "status_code", None) == 429:
                logger.warning("Nominatim 429; waiting %.0fs then retry.", NOMINATIM_RETRY_DELAY_SEC)
                time.sleep(NOMINATIM_RETRY_DELAY_SEC)
                continue
            logger.warning("Nominatim reverse geocoding failed: %s", e)
            return out
    if last_err is not None:
        return out

    addr = data.get("address") or {}
    if not isinstance(addr, dict):
        return out

    # Map Nominatim keys to our fields (keys vary by region; India often uses state_district, etc.)
    out["village"] = _first(addr, ["village", "hamlet", "locality", "suburb"])
    out["town"] = _first(addr, ["town", "city", "municipality"])
    out["district"] = _first(addr, ["county", "district", "state_district", "subdistrict"])
    out["state"] = _first(addr, ["state", "region", "ISO3166-2-lvl4"])
    out["country"] = _first(addr, ["country"])
    out["postcode"] = _first(addr, ["postcode", "postal_code"])
    return out


def _first(d: dict, keys: list[str]) -> Optional[str]:
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None
