"""
Extract village, grower, variety from KML placemark name.
Format: village-{VillageName} {GrowerName} {VarietyName}
Example: village-Banjari Gumesh kumar sahu Usrh-24 -> village=Banjari, grower=Gumesh kumar sahu, variety=Usrh-24
"""

from __future__ import annotations

import re
from typing import Optional


def extract_metadata(placemark_name: str) -> dict[str, Optional[str]]:
    """
    Parse placemark name and return village, grower, variety.
    Returns dict with keys "village", "grower", "variety"; missing values are None.
    """
    out = {"village": None, "grower": None, "variety": None}
    if not placemark_name or not isinstance(placemark_name, str):
        return out
    s = placemark_name.strip()
    if not s.startswith("village-"):
        return out
    rest = s[8:].strip()  # after "village-" (8 chars: v-i-l-l-a-g-e-)
    parts = rest.split()
    if len(parts) == 0:
        return out
    if len(parts) == 1:
        out["village"] = parts[0]
        return out
    if len(parts) == 2:
        out["village"] = parts[0]
        out["variety"] = parts[1]
        return out
    out["village"] = parts[0]
    out["variety"] = parts[-1]
    out["grower"] = " ".join(parts[1:-1])
    return out
