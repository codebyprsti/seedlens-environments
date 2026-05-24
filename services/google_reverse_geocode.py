"""
Reverse geocoding using Google Maps Geocoding API.
Returns village, district, state, mandal, postalcode, country (and state_code for ID generation).
Same contract as location_resolver.get_location_from_coordinates for pipeline compatibility.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def get_location_from_coordinates(lat: float, lon: float) -> dict[str, Optional[str]]:
    """
    Reverse geocode (lat, lon) using Google Maps Geocoding API.
    Returns village, town, district, state, state_code, mandal, country, postcode.
    On API failure or missing key returns empty dict values.
    """
    out: dict[str, Optional[str]] = {
        "village": None,
        "town": None,
        "district": None,
        "state": None,
        "state_code": None,
        "mandal": None,
        "country": None,
        "postcode": None,
    }
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        logger.warning("Google reverse geocode: GOOGLE_API_KEY (or GOOGLE_MAPS_API_KEY) not set")
        return out

    try:
        resp = requests.get(
            GOOGLE_GEOCODE_URL,
            params={"latlng": f"{lat},{lon}", "key": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("Google reverse geocoding failed: %s", e)
        return out

    if data.get("status") != "OK":
        logger.debug("Google geocode status: %s", data.get("status"))
        return out

    results = data.get("results") or []
    if not results:
        return out

    # Use first result and parse address_components
    comps = results[0].get("address_components") or []
    for c in comps:
        types = c.get("types") or []
        long_name = (c.get("long_name") or "").strip()
        short_name = (c.get("short_name") or "").strip()
        if not long_name:
            continue
        if "locality" in types or "sublocality" in types or "sublocality_level_1" in types:
            if not out["village"]:
                out["village"] = long_name
        elif "administrative_area_level_3" in types:
            out["mandal"] = long_name
        elif "administrative_area_level_2" in types:
            out["district"] = long_name
        elif "administrative_area_level_1" in types:
            out["state"] = long_name
            out["state_code"] = short_name if short_name else long_name
        elif "postal_code" in types:
            out["postcode"] = long_name
        elif "country" in types:
            out["country"] = long_name

    # Fallback: use political or locality for village if still missing
    if not out["village"]:
        for c in comps:
            types = c.get("types") or []
            long_name = (c.get("long_name") or "").strip()
            if long_name and ("locality" in types or "sublocality" in types or "neighborhood" in types):
                out["village"] = long_name
                break

    # town: prefer locality/city
    if not out["town"]:
        for c in comps:
            types = c.get("types") or []
            long_name = (c.get("long_name") or "").strip()
            if long_name and ("locality" in types or "administrative_area_level_2" in types):
                out["town"] = long_name
                break

    return out
