"""
Extract village_name and grower_name from unstructured KML metadata strings.
Multi-stage pipeline: clean -> remove variety codes -> village markers -> NER (spaCy) -> fallback token logic.
"""

from __future__ import annotations

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Variety code pattern: USRH-24, USRH24, US24, US-24, USRH_24
VARIETY_PATTERN = re.compile(r"US[A-Z]*[-_\s]?\d+", re.IGNORECASE)

# Village explicit marker: Village-, Village:, Village_, Village<space> (capture first word or rest)
VILLAGE_MARKER_PATTERN = re.compile(
    r"Village[-:_\s]+([A-Za-z]+)",
    re.IGNORECASE,
)

# Optional: spaCy for PERSON NER (lazy load)
_nlp = None
_SPACY_AVAILABLE: Optional[bool] = None


def _get_nlp():
    global _nlp, _SPACY_AVAILABLE
    if _SPACY_AVAILABLE is not None:
        return _nlp
    try:
        import spacy
        _nlp = spacy.load("en_core_web_sm")
        _SPACY_AVAILABLE = True
    except Exception as e:
        logger.debug("spaCy not available for entity extraction: %s", e)
        _nlp = None
        _SPACY_AVAILABLE = False
    return _nlp


def _clean_and_remove_variety(text: str) -> str:
    """Step 1: Strip and remove crop variety codes."""
    if not text or not isinstance(text, str):
        return ""
    t = text.strip()
    t = VARIETY_PATTERN.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _detect_village_by_marker(text: str) -> Optional[str]:
    """Step 2: Extract village from explicit 'Village-', 'Village:', etc."""
    if not text:
        return None
    m = VILLAGE_MARKER_PATTERN.search(text)
    if m:
        return m.group(1).strip()
    return None


def _detect_person_spans(text: str) -> list[tuple[int, int, str]]:
    """Step 3: Use spaCy NER to find PERSON entities. Returns [(start, end, text), ...]."""
    if not text or not text.strip():
        return []
    nlp = _get_nlp()
    if nlp is None:
        return []
    try:
        doc = nlp(text.strip())
        return [(ent.start_char, ent.end_char, ent.text.strip()) for ent in doc.ents if ent.label_ == "PERSON"]
    except Exception as e:
        logger.debug("spaCy NER failed: %s", e)
        return []


def _fallback_village_and_grower(cleaned: str, person_spans: list[tuple[int, int, str]]) -> tuple[Optional[str], Optional[str], float]:
    """
    Step 4: When no village marker, use token / PERSON logic.
    Returns (village_name, grower_name, confidence).
    """
    if not cleaned:
        return None, None, 0.0

    tokens = cleaned.split()
    if not tokens:
        return None, None, 0.0

    grower_name = None
    if person_spans:
        # Longest PERSON entity as grower
        longest = max(person_spans, key=lambda x: len(x[2]))
        grower_name = longest[2].strip()

    # Village: tokens before first PERSON, or after last PERSON, or first token
    village_name = None
    if person_spans:
        first_start = person_spans[0][0]
        last_end = person_spans[-1][1]
        # Text before first PERSON
        before = cleaned[:first_start].strip()
        # Text after last PERSON
        after = cleaned[last_end:].strip()
        if before and not before.isspace():
            village_name = before.split()[0] if before.split() else before  # first word before PERSON
        elif after and not after.isspace():
            village_name = after.split()[0] if after.split() else after  # first word after PERSON
    if not village_name and tokens:
        village_name = tokens[0]
    if not grower_name and len(tokens) >= 2:
        # No NER: assume first token = village, rest = grower
        village_name = tokens[0]
        grower_name = " ".join(tokens[1:])

    confidence = 0.85 if person_spans else 0.65
    return village_name, grower_name, confidence


def _normalize_village(s: Optional[str]) -> Optional[str]:
    """Step 5a: Capitalize first letter, remove punctuation, strip."""
    if not s or not str(s).strip():
        return None
    t = re.sub(r"[^\w\s]", "", str(s).strip())
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    return t[0].upper() + t[1:].lower() if len(t) > 1 else t.upper()


def _normalize_grower(s: Optional[str]) -> Optional[str]:
    """Step 5b: Title case, remove extra spaces."""
    if not s or not str(s).strip():
        return None
    t = re.sub(r"\s+", " ", str(s).strip()).strip()
    return t.title() if t else None


def extract_village_and_grower(text: Optional[str]) -> dict[str, Optional[str] | float]:
    """
    Extract village_name and grower_name from unstructured metadata (e.g. KML placemark name).

    Multi-stage pipeline:
      1. Clean and remove variety codes (USRH-24, US24, etc.)
      2. Detect village via explicit markers (Village-, Village:, etc.)
      3. Detect grower via spaCy NER (PERSON)
      4. Fallback: token logic (tokens before/after PERSON, or first token = village)

    Returns:
        {"village_name": str|None, "grower_name": str|None, "confidence": float}
        confidence: 0.95 (marker), 0.85 (NER+token), 0.65 (first-token fallback), 0 (fail)
    """
    out: dict[str, Optional[str] | float] = {
        "village_name": None,
        "grower_name": None,
        "confidence": 0.0,
    }
    if text is None:
        return out
    if not isinstance(text, str) or not text.strip():
        return out

    cleaned = _clean_and_remove_variety(text)
    if not cleaned:
        return out

    village_from_marker = _detect_village_by_marker(text)  # use original text for marker
    person_spans = _detect_person_spans(cleaned)

    village_name = None
    grower_name = None
    confidence = 0.0

    if village_from_marker:
        village_name = _normalize_village(village_from_marker)
        confidence = 0.95
        if person_spans:
            longest = max(person_spans, key=lambda x: len(x[2]))
            grower_name = _normalize_grower(longest[2])
        else:
            # Remove village part from cleaned and use rest as grower (or single token)
            rest = VILLAGE_MARKER_PATTERN.sub("", text).strip()
            rest = VARIETY_PATTERN.sub("", rest)
            rest = re.sub(r"\s+", " ", rest).strip()
            if rest and rest.lower() != village_name.lower():
                grower_name = _normalize_grower(rest)
    else:
        v, g, c = _fallback_village_and_grower(cleaned, person_spans)
        village_name = _normalize_village(v) if v else None
        grower_name = _normalize_grower(g) if g else None
        confidence = c

    # Final step: strip any remaining variety code from both fields (e.g. from NER span)
    if village_name:
        village_name = _normalize_village(VARIETY_PATTERN.sub("", village_name).strip()) or village_name
    if grower_name:
        stripped = VARIETY_PATTERN.sub("", grower_name).strip()
        stripped = re.sub(r"\s+", " ", stripped).strip()
        grower_name = _normalize_grower(stripped) if stripped else None

    out["village_name"] = village_name
    out["grower_name"] = grower_name
    out["confidence"] = round(confidence, 2)
    return out
