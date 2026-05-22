"""
Multi-stage ID resolution: text normalization, variety standardization, fuzzy matching.
Used by repository to resolve grower_id, variety_id from inconsistent KML/filename data.
(Location matching uses operations.field_locations + centroid, not this module.)
"""

from __future__ import annotations

import re
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Fuzzy matching: optional dependency
try:
    from rapidfuzz import fuzz
    from rapidfuzz import process as rf_process
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    _RAPIDFUZZ_AVAILABLE = False


# --- 1. Text normalization ---

def normalize_text(s: str) -> str:
    """
    Normalize for matching: lowercase, remove punctuation/special chars, collapse spaces.
    Examples: "JHALJHALIYA" -> "jhaljhalia", "USRH-24" -> "usrh24"
    """
    if not s or not isinstance(s, str):
        return ""
    t = s.strip().lower()
    # Remove punctuation and special characters (keep alphanumeric and spaces)
    t = re.sub(r"[^\w\s]", "", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# --- 2. Variety standardization ---
# Final standard format: USRH-<number>. US24, US 24, US-24, USRH24, USRH-24 -> USRH-24

_VARIETY_PATTERNS = [
    (re.compile(r"usrh\s*\-?\s*(\d+)", re.I), "USRH-{}"),
    (re.compile(r"us\s*\-?\s*(\d+)", re.I), "USRH-{}"),
]


def normalize_variety_name(s: str) -> str:
    """
    Normalize variety name to standard form before matching or inserting.
    Examples: US24, US 24, US-24, USRH24, USRH-24 -> USRH-24; US26 -> USRH-26.
    """
    return normalize_variety(s)


def normalize_variety(s: str) -> str:
    """
    Convert variety input to canonical form (USRH-<number> for US/USRH patterns).
    Examples: usrh24, usrh-24, us 24, us24 -> USRH-24; us26 -> USRH-26
    """
    if not s or not isinstance(s, str):
        return ""
    t = s.strip()
    for pat, fmt in _VARIETY_PATTERNS:
        m = pat.search(t)
        if m:
            num = m.group(1)
            return fmt.format(num)
    # Fallback: normalize text only (capitalize first letter per word if needed)
    return normalize_text(t)


def variety_canonical_for_fuzzy(s: str) -> str:
    """Canonical form for fuzzy comparison (lowercase, no extra spaces)."""
    c = normalize_variety(s)
    if not c:
        return normalize_text(s)
    return c.strip().lower()


# --- 3 & 4. Fuzzy matching ---

def _fuzzy_best(
    query: str,
    candidates: List[Tuple[str, str, str]],  # (id, display_name, normalized_name)
    threshold: float,
    scorer=None,
) -> Optional[Tuple[str, str, float]]:
    """
    Return (matched_id, matched_display_name, score) if best score >= threshold, else None.
    candidates: list of (id, display_name, normalized_name).
    """
    if not _RAPIDFUZZ_AVAILABLE or not query or not candidates:
        return None
    scorer = scorer or fuzz.ratio
    names_norm = [c[2] for c in candidates]
    result = rf_process.extractOne(query, names_norm, scorer=scorer)
    if not result:
        return None
    matched_norm, score, idx = result
    if score < threshold:
        return None
    return (candidates[idx][0], candidates[idx][1], float(score))


def fuzzy_match_grower(
    db_session,
    grower_input: str,
    location_id: Optional[str] = None,
    threshold: float = 90.0,
) -> Optional[Tuple[str, str, float]]:
    """
    Fetch growers (optionally filtered by location_id via inspection), normalize, fuzzy match.
    Returns (grower_id, matched_grower_name, score) or None.
    """
    from sqlalchemy import text
    if not grower_input or not grower_input.strip():
        return None
    norm_input = normalize_text(grower_input)
    if location_id is not None:
        try:
            rows = db_session.execute(
                text("""
                    SELECT g.grower_id, g.grower_name
                    FROM operations.growers g
                    WHERE EXISTS (
                        SELECT 1 FROM operations.season_crop_inspection_base i
                        WHERE i.grower_id = g.grower_id AND i.location_id = :loc
                    )
                """),
                {"loc": location_id},
            ).fetchall()
        except Exception:
            rows = []
    else:
        rows = db_session.execute(
            text("SELECT grower_id, grower_name FROM operations.growers WHERE grower_name IS NOT NULL AND TRIM(grower_name) != ''"),
        ).fetchall()
    candidates = [(r[0], r[1], normalize_text(r[1] or "")) for r in rows if (r[1] or "").strip()]
    if not candidates:
        return None
    return _fuzzy_best(norm_input, candidates, threshold)


def fuzzy_match_variety(
    db_session,
    variety_input: str,
    threshold: float = 85.0,
) -> Optional[Tuple[str, str, float]]:
    """
    Fetch all varieties, normalize (canonical + text), fuzzy match.
    Returns (variety_id, matched_variety_name, score) or None.
    """
    from sqlalchemy import text
    if not variety_input or not variety_input.strip():
        return None
    # Try canonical form first for display; for matching we use normalized text
    norm_input = variety_canonical_for_fuzzy(variety_input)
    if not norm_input:
        norm_input = normalize_text(variety_input)
    rows = db_session.execute(
        text("SELECT variety_id, variety_name FROM operations.varieties WHERE variety_name IS NOT NULL AND TRIM(variety_name) != ''"),
    ).fetchall()
    # Each candidate: normalized for comparison (canonical form + plain normalize)
    candidates = []
    for r in rows:
        v = (r[1] or "").strip()
        if not v:
            continue
        norm_v = variety_canonical_for_fuzzy(v)
        if not norm_v:
            norm_v = normalize_text(v)
        candidates.append((r[0], v, norm_v))
    if not candidates:
        return None
    return _fuzzy_best(norm_input, candidates, threshold)
