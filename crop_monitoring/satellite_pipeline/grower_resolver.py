"""
Resolve grower_name → operations.growers.grower_id (G_<n> sequence).
Reuses existing growers; creates new rows when no match. Session-level cache.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from crop_monitoring.database.id_resolution import normalize_text, fuzzy_match_grower
from crop_monitoring.database.master_repository import CATEGORY_GROWER, get_next_grower_id

logger = logging.getLogger(__name__)

GROWER_MATCH_THRESHOLD = 85.0
_GROWER_ID_PATTERN = re.compile(r"^G_\d+$", re.I)


def normalize_grower_name(name: Optional[str]) -> str:
    """Trim, collapse spaces, lowercase for comparison."""
    if not name:
        return ""
    return normalize_text(str(name).strip())


def is_db_grower_id(value: Optional[str]) -> bool:
    """True if value matches operations.growers format G_<digits>."""
    return bool(value and _GROWER_ID_PATTERN.match(str(value).strip()))


def _exact_grower_lookup(db: Session, grower_name: str) -> Optional[tuple[str, str]]:
    norm = normalize_grower_name(grower_name)
    if not norm:
        return None
    row = db.execute(
        text("""
            SELECT grower_id, grower_name
            FROM operations.growers
            WHERE LOWER(REGEXP_REPLACE(TRIM(grower_name), '\\s+', ' ', 'g')) = :norm
            ORDER BY grower_id
            LIMIT 1
        """),
        {"norm": norm},
    ).fetchone()
    if row:
        return str(row[0]), str(row[1])
    return None


def insert_grower_no_commit(db: Session, grower_name: str) -> str:
    """Insert grower row without committing (caller commits)."""
    name_clean = str(grower_name).strip()[:200]
    if not name_clean:
        raise ValueError("grower_name required")
    grower_id = get_next_grower_id(db)
    db.execute(
        text("""
            INSERT INTO operations.growers (grower_id, grower_name, fathers_name, grower_gender, category_id)
            VALUES (:grower_id, :grower_name, NULL, NULL, :category_id)
        """),
        {"grower_id": grower_id, "grower_name": name_clean, "category_id": CATEGORY_GROWER},
    )
    logger.info("Created grower %s for %s", grower_id, name_clean)
    return grower_id


class GrowerResolver:
    """Per-session cache: normalized name → (grower_id, canonical_name)."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._cache: dict[str, tuple[str, str]] = {}
        self.created_ids: list[str] = []

    def resolve(
        self,
        grower_name: Optional[str],
        *,
        location_id: Optional[str] = None,
        excel_grower_id_hint: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Returns (grower_id, grower_name). grower_id is always G_* when name is present.
        excel_grower_id_hint is ignored unless it matches G_* and exists in DB.
        """
        if not grower_name or not str(grower_name).strip():
            return None, None

        canonical = str(grower_name).strip()
        norm = normalize_grower_name(canonical)
        if norm in self._cache:
            return self._cache[norm]

        if excel_grower_id_hint and is_db_grower_id(excel_grower_id_hint):
            row = self.db.execute(
                text("""
                    SELECT grower_id, grower_name FROM operations.growers
                    WHERE grower_id = :gid LIMIT 1
                """),
                {"gid": excel_grower_id_hint.strip().upper()},
            ).fetchone()
            if row:
                out = (str(row[0]), str(row[1]))
                self._cache[norm] = out
                return out

        exact = _exact_grower_lookup(self.db, canonical)
        if exact:
            self._cache[norm] = exact
            return exact

        fuzzy = fuzzy_match_grower(
            self.db, canonical, location_id, threshold=GROWER_MATCH_THRESHOLD
        )
        if fuzzy:
            gid, matched, _score = fuzzy
            out = (gid, matched)
            self._cache[norm] = out
            return out

        gid = insert_grower_no_commit(self.db, canonical)
        self.created_ids.append(gid)
        out = (gid, canonical)
        self._cache[norm] = out
        return out


def ensure_grower_linked(
    db: Session,
    *,
    grower_name: Optional[str],
    grower_id: Optional[str] = None,
    location_id: Optional[str] = None,
    excel_grower_id_hint: Optional[str] = None,
    resolver: Optional[GrowerResolver] = None,
) -> tuple[Optional[str], Optional[str], GrowerResolver]:
    """
    Returns (grower_id, grower_name, resolver). Resolves when name present but id missing.
    """
    res = resolver or GrowerResolver(db)
    if grower_id:
        return grower_id, grower_name, res
    if not grower_name or not str(grower_name).strip():
        return None, None, res
    gid, canonical = res.resolve(
        grower_name,
        location_id=location_id,
        excel_grower_id_hint=excel_grower_id_hint,
    )
    return gid, canonical, res
