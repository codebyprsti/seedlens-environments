"""
Resolve village, grower_name, variety from inconsistent KML <name> and filename.
Order: 1) KML name (regex), 2) filename (regex), 3) optional LLM, 4) reverse geocode / DB fallbacks.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Variety: USRH-24, USRH24, US 26, us26, etc.
VARIETY_PATTERN = re.compile(
    r"(USRH\s*-?\s*\d+|US\s*\d+)\s*(?:p\d+)?",
    re.IGNORECASE,
)

# Normalize variety to USRH24 or US26
def _normalize_variety(raw: str) -> str:
    if not raw:
        return ""
    s = re.sub(r"\s+", "", raw.upper())
    if "USRH" in s:
        return "USRH" + (re.search(r"\d+", s).group(0) if re.search(r"\d+", s) else "")
    if s.startswith("US"):
        return "US" + (re.search(r"\d+", s).group(0) if re.search(r"\d+", s) else "")
    return s


def _extract_from_text(text: str) -> dict[str, Optional[str]]:
    """Extract village (first word), variety (regex), grower (remaining) from a single string."""
    out = {"village": None, "grower_name": None, "variety": None}
    if not text or not isinstance(text, str):
        return out
    text = text.strip()
    if not text:
        return out
    # Optional "village-" prefix
    if text.lower().startswith("village-"):
        text = text[8:].strip()
    parts = text.split()
    if not parts:
        return out
    # Find variety by regex (take first match)
    variety_match = VARIETY_PATTERN.search(text)
    variety_raw = variety_match.group(0).strip() if variety_match else None
    variety = _normalize_variety(variety_raw) if variety_raw else None
    # Remove variety substring from text for village/grower
    text_without_variety = VARIETY_PATTERN.sub("", text, count=1).strip()
    parts_no_var = text_without_variety.split()
    if not parts_no_var:
        out["variety"] = variety
        return out
    village = parts_no_var[0] if parts_no_var else None
    grower_tokens = parts_no_var[1:] if len(parts_no_var) > 1 else []
    grower_name = " ".join(grower_tokens).strip() if grower_tokens else None
    out["village"] = village
    out["grower_name"] = grower_name or None
    out["variety"] = variety
    return out


def _try_llm_extraction(raw_text: str) -> Optional[dict[str, Optional[str]]]:
    """
    Optional: call OpenAI-compatible API (SambaNova or free model) to extract village, grower_name, variety.
    SambaNova model (second priority): ca61779e-b311-42a8-9121-8569abbdb046.
    """
    base_url = os.environ.get("LLM_API_URL") or os.environ.get("OPENAI_BASE_URL") or ""
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    model = os.environ.get("LLM_MODEL") or "ca61779e-b311-42a8-9121-8569abbdb046"
    if not base_url:
        return None
    try:
        import requests
        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        prompt = (
            f"From this farm record text, extract exactly three values. Reply in one line with only: village|grower_name|variety\n"
            f"Use | as separator. If a value is missing use empty. Normalize variety to form like USRH24 or US26.\n"
            f"Text: {raw_text}\n"
            f"Reply:"
        )
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        r = requests.post(
            url,
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 150, "temperature": 0},
            headers=headers,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        if not content:
            return None
        parts = [p.strip() for p in content.split("|")][:3]
        village = parts[0] if len(parts) > 0 and parts[0] else None
        grower_name = parts[1] if len(parts) > 1 and parts[1] else None
        variety = parts[2] if len(parts) > 2 and parts[2] else None
        if variety:
            variety = _normalize_variety(variety)
        return {"village": village, "grower_name": grower_name, "variety": variety}
    except Exception as e:
        logger.debug("LLM metadata extraction failed: %s", e)
        return None


def resolve_metadata(
    kml_name: str = "",
    filename: str = "",
    *,
    db_session=None,
    detected_location: Optional[dict[str, Any]] = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """
    Resolve village, grower_name, variety from KML name and filename.
    Order: 1) KML name (regex), 2) filename (regex), 3) optional LLM, 4) reverse geocode village fallback, 5) DB validation (grower/variety only; not location).
    Returns dict with village, grower_name, variety and confidence (village, grower, variety floats 0-1).
    """
    out = {
        "village": None,
        "grower_name": None,
        "variety": None,
        "confidence": {"village": 0.0, "grower": 0.0, "variety": 0.0},
    }
    # 1) Try KML name first
    text = (kml_name or "").strip()
    if text:
        extracted = _extract_from_text(text)
        if any(extracted.values()):
            out["village"] = extracted.get("village")
            out["grower_name"] = extracted.get("grower_name")
            out["variety"] = extracted.get("variety")
            out["confidence"] = {"village": 0.8, "grower": 0.7, "variety": 0.95}
    fname_clean = (str(filename).replace(".kml", "").replace(".KML", "").strip() if filename else "")
    # 2) Fill missing from filename
    if fname_clean:
        if fname_clean and (not out["village"] or not out["grower_name"] or not out["variety"]):
            from_fn = _extract_from_text(fname_clean)
            if not out["village"] and from_fn.get("village"):
                out["village"] = from_fn["village"]
                out["confidence"]["village"] = max(out["confidence"]["village"], 0.7)
            if not out["grower_name"] and from_fn.get("grower_name"):
                out["grower_name"] = from_fn["grower_name"]
                out["confidence"]["grower"] = max(out["confidence"]["grower"], 0.6)
            if not out["variety"] and from_fn.get("variety"):
                out["variety"] = from_fn["variety"]
                out["confidence"]["variety"] = max(out["confidence"]["variety"], 0.9)
    # 3) Optional LLM when still missing or low confidence
    if use_llm and (not out["village"] or not out["grower_name"] or not out["variety"]):
        raw = text or fname_clean
        if raw:
            llm_result = _try_llm_extraction(raw)
            if llm_result:
                if not out["village"] and llm_result.get("village"):
                    out["village"] = llm_result["village"]
                    out["confidence"]["village"] = 0.85
                if not out["grower_name"] and llm_result.get("grower_name"):
                    out["grower_name"] = llm_result["grower_name"]
                    out["confidence"]["grower"] = 0.8
                if not out["variety"] and llm_result.get("variety"):
                    out["variety"] = llm_result["variety"]
                    out["confidence"]["variety"] = 0.95
    # 4) Village fallback from reverse geocode
    if not out["village"] and detected_location:
        geo_village = (detected_location.get("village") or "").strip()
        if geo_village and len(geo_village) >= 2:
            out["village"] = geo_village
            out["confidence"]["village"] = max(out["confidence"]["village"], 0.6)
    # 5) DB validation for grower / variety only (location_id must come from operations.field_locations + centroid; never operations.locations)
    if db_session:
        try:
            from crop_monitoring.database.repository import get_grower_id, get_variety_id

            if out["grower_name"]:
                grower_id = get_grower_id(db_session, out["grower_name"], None)
                if grower_id:
                    out["confidence"]["grower"] = min(1.0, out["confidence"]["grower"] + 0.2)
            if out["variety"]:
                var_id = get_variety_id(db_session, out["variety"])
                if var_id:
                    out["confidence"]["variety"] = min(1.0, out["confidence"]["variety"] + 0.05)
        except Exception as e:
            logger.debug("DB validation for metadata failed: %s", e)
    # Grower fallback: if still empty, store raw middle part from first source
    if not out["grower_name"] and text:
        ext = _extract_from_text(text)
        if ext.get("grower_name"):
            out["grower_name"] = ext["grower_name"]
        else:
            # Last resort: remove variety and take rest after first word as grower
            no_var = VARIETY_PATTERN.sub("", text, count=1).strip()
            tokens = no_var.split()
            if len(tokens) > 1:
                out["grower_name"] = " ".join(tokens[1:])
                out["confidence"]["grower"] = 0.5
    return out
