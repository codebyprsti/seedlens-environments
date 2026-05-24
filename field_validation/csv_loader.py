"""
Load Data Entry_Field Team-style CSV; filter rows with both satellite SYNC and NDVI filled.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd


def _norm_col(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip()).lower()


def _resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in df.columns:
        n = _norm_col(c)
        if n == "field id":
            out["field_id"] = c
        elif "field name" in n and "kml" in n:
            out["field_name_kml"] = c
        elif "sync" in n and "sattelite" in n:
            out["sync_satellite"] = c
        elif "ndvi" in n and "sattelite" in n:
            out["ndvi_satellite"] = c
        elif "validation date" in n:
            out["validation_date"] = c
        elif n == "state":
            out["state"] = c
    missing = [
        k
        for k in ("field_id", "field_name_kml", "sync_satellite", "ndvi_satellite")
        if k not in out
    ]
    if missing:
        raise ValueError(
            f"Could not resolve columns {missing}. "
            f"Available (first 20): {list(df.columns[:20])!r}"
        )
    return out


def load_field_team_csv(path: str | Path) -> pd.DataFrame:
    """Read CSV as strings; tolerate encoding issues."""
    p = Path(path)
    return pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8", encoding_errors="replace")


def filter_satellite_validated_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Rows where both SYNC (Satellite) and NDVI (Satellite) are non-empty after strip."""
    cols = _resolve_columns(df)

    def nonempty(x: Any) -> bool:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return False
        s = str(x).strip()
        return bool(s) and s.lower() not in ("nan", "none", "null")

    m = df[cols["sync_satellite"]].map(nonempty) & df[cols["ndvi_satellite"]].map(nonempty)
    return df.loc[m].copy()


def get_filtered_with_location_ids(
    df: pd.DataFrame,
    *,
    require_satellite_pair: bool = True,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Apply optional satellite filter (SYNC + NDVI satellite both filled).
    If require_satellite_pair is False, keep all rows with non-empty Field ID.
    """
    cols = _resolve_columns(df)
    fid_col = cols["field_id"]
    if require_satellite_pair:
        sub = filter_satellite_validated_rows(df)
    else:
        sub = df[df[fid_col].astype(str).str.strip() != ""].copy()
    cols = _resolve_columns(sub)
    fid_col = cols["field_id"]
    ids = sorted({str(x).strip() for x in sub[fid_col] if str(x).strip()})
    return sub, ids


def first_row_for_location_id(df: pd.DataFrame, location_id: str) -> Optional[dict[str, str]]:
    """First matching row dict for a Field ID."""
    cols = _resolve_columns(df)
    fid = cols["field_id"]
    sub = df[df[fid].astype(str).str.strip() == str(location_id).strip()]
    if sub.empty:
        return None
    return row_dict(sub.reset_index(drop=True), 0)


def row_dict(df: pd.DataFrame, idx: int) -> dict[str, str]:
    """One row as dict with stable keys."""
    cols = _resolve_columns(df)
    r = df.iloc[idx]
    state_col = cols.get("state")
    vd_col = cols.get("validation_date")
    loc = str(r[cols["field_id"]]).strip()
    return {
        "state": str(r[state_col]).strip() if state_col else "",
        "location_id": loc,
        "field_name_kml": str(r[cols["field_name_kml"]]).strip(),
        "csv_sync_satellite": str(r[cols["sync_satellite"]]).strip(),
        "csv_ndvi_satellite": str(r[cols["ndvi_satellite"]]).strip(),
        "csv_validation_date": str(r[vd_col]).strip() if vd_col else "",
    }
