"""
Export crop indices to CSV/JSON for reporting and dashboard integration.

Aligns with document: "Export or manually tabulate", "Spreadsheet".
"""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Any, Optional


def to_csv(
    rows: list[dict[str, Any]],
    fieldnames: Optional[list[str]] = None,
    *,
    dialect: str = "excel",
) -> str:
    """Format rows as CSV. Uses first row keys if fieldnames not given."""
    if not rows:
        return ""
    fieldnames = fieldnames or list(rows[0].keys())
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", dialect=dialect)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def to_json(rows: list[dict[str, Any]], *, indent: Optional[int] = 2) -> str:
    """Format rows as JSON for API or file export."""
    return json.dumps(rows, indent=indent, default=str)


def summary_report(
    batch_results: list[dict[str, Any]],
    *,
    total: int = 0,
    failed: int = 0,
    failed_files: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Produce a summary dict for a batch run (e.g. for logging or API)."""
    return {
        "total": total,
        "success": len(batch_results),
        "failed": failed,
        "failed_files": failed_files or [],
        "results_count": len(batch_results),
    }
