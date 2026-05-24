"""Parse field-form validation dates (e.g. 01-Apr without year)."""

from __future__ import annotations

from datetime import date
from typing import Optional


def parse_validation_date_cell(value: str, *, default_year: int) -> Optional[date]:
    """
    Parse CSV validation cells like '01-Apr', '31-Mar', or full ISO dates.
    Uses default_year when the cell has no year.
    """
    from datetime import datetime

    s = (value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    for fmt in ("%d-%b", "%d-%B"):
        try:
            return datetime.strptime(f"{s}-{default_year}", f"{fmt}-%Y").date()
        except ValueError:
            continue
    return None
