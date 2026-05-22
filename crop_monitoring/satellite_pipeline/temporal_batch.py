"""Temporal chunking for bulk Statistical API (weekly / monthly / quarterly)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterator, Literal

BatchStrategy = Literal["weekly", "monthly", "quarterly", "full"]


def calendar_dates_inclusive(start: date, end: date) -> list[str]:
    """Every calendar day from start through end (ISO date strings)."""
    if start > end:
        return []
    out: list[str] = []
    d = start
    while d <= end:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def chunk_date_range(
    start: date,
    end: date,
    strategy: BatchStrategy = "quarterly",
) -> list[tuple[date, date]]:
    """Inclusive start/end chunks for API requests."""
    if strategy == "full" or start >= end:
        return [(start, end)]

    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        if strategy == "weekly":
            chunk_end = min(cur + timedelta(days=6), end)
        elif strategy == "monthly":
            if cur.month == 12:
                next_m = date(cur.year + 1, 1, 1)
            else:
                next_m = date(cur.year, cur.month + 1, 1)
            chunk_end = min(next_m - timedelta(days=1), end)
        else:  # quarterly ~90 days
            chunk_end = min(cur + timedelta(days=89), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    return chunks
