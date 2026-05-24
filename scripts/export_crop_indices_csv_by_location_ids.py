#!/usr/bin/env python3
"""
Export crop_indices data to CSV for a list of location_ids (validation utility).

Outputs:
1) Detailed rows CSV (crop_indices joined with field_locations where available)
2) Summary CSV (counts per location_id, file_name)

Design:
- Schema-robust: detects existing columns via information_schema and only exports available fields.
- Read-only: does not modify DB.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
from pathlib import Path
from typing import Iterable

from sqlalchemy import text

_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(_ROOT))

from core.db import SessionLocal  # noqa: E402
from scripts.export_kmls_by_location_ids import (  # noqa: E402
    DEFAULT_LOCATION_IDS,
    _read_location_ids_file,
)

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _table_columns(db, *, schema: str, table: str) -> set[str]:
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            """
        ),
        {"schema": schema, "table": table},
    ).fetchall()
    return {str(r[0]) for r in rows if r and r[0]}


_SAFE_COL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _safe_ident(name: str) -> str:
    if not _SAFE_COL.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def _write_csv(path: Path, headers: list[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(description="Export crop_indices CSV for given location_ids.")
    parser.add_argument("--location-ids-file", type=str, default=None, help="Text file with one location_id per line")
    parser.add_argument(
        "--output-dir",
        type=str,
        default=r"C:\Users\madan\Downloads\kml_location_files",
        help="Output directory for CSVs",
    )
    parser.add_argument("--season-id", type=str, default=None, help="Optional season filter (e.g. RABI_25_26)")
    args = parser.parse_args()

    location_ids = _read_location_ids_file(args.location_ids_file) if args.location_ids_file else list(DEFAULT_LOCATION_IDS)
    location_ids = [s.strip() for s in location_ids if s and s.strip() and s.strip().lower() != "location_id"]
    location_ids = list(dict.fromkeys(location_ids))
    if not location_ids:
        raise SystemExit("No location_ids provided.")

    out_dir = Path(args.output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        ci_cols = _table_columns(db, schema="operations", table="crop_indices")
        fl_cols = _table_columns(db, schema="operations", table="field_locations")

        # Prefer these columns for validation (export what exists)
        desired_ci = [
            "id",
            "season_id",
            "file_name",
            "analysis_date",
            "location_id",
            "date_start",
            "date_end",
            "polygon_id",
            "polygon_area",
            "area_acre",
            "distance_km",
            "crop_id",
            "crop_name",
            "grower_id",
            "variety_id",
            "grower_name",
            "variety_name",
            # S2 indices
            "ndvi",
            "ndwi",
            "savi",
            "ndmi",
            "ndre",
            "gci",
            "psri",
            "msavi",
            "evi",
            "lai",
            "ndwi_gao",
            # SAR/LST (targets)
            "lst_celsius",
            "vv_db",
            "vh_db",
            "vh_vv_ratio",
            # Band means
            "blue",
            "green",
            "red",
            "rededge1",
            "rededge2",
            "rededge3",
            "nir",
            "narrow_nir",
            "swir1",
            "swir2",
            # Timestamps
            "created_at",
        ]
        desired_fl = [
            "location_id",
            "extracted_village",
            "village",
            "district",
            "state",
            "state_code",
            "mandal",
            "postalcode",
            "latitude",
            "longitude",
        ]

        select_ci = [c for c in desired_ci if c in ci_cols]
        # Always ensure join keys if possible
        if "location_id" in ci_cols and "location_id" not in select_ci:
            select_ci.append("location_id")
        if "file_name" in ci_cols and "file_name" not in select_ci:
            select_ci.append("file_name")
        if "analysis_date" in ci_cols and "analysis_date" not in select_ci:
            select_ci.append("analysis_date")

        select_fl = [c for c in desired_fl if c in fl_cols]

        # Build SQL SELECT list with aliases to avoid collisions (location_id exists in both)
        ci_select_sql = [f"ci.{_safe_ident(c)} AS ci_{c}" for c in select_ci]
        fl_select_sql = [f"fl.{_safe_ident(c)} AS fl_{c}" for c in select_fl]
        select_sql = ",\n  ".join(ci_select_sql + fl_select_sql)

        logger.info("Exporting crop_indices columns: %d", len(select_ci))
        logger.info("Exporting field_locations columns: %d", len(select_fl))

        detailed_rows: list[dict] = []

        for chunk in _chunks(location_ids, 500):
            params = {f"lid_{i}": v for i, v in enumerate(chunk)}
            in_list = ", ".join(f":lid_{i}" for i in range(len(chunk)))
            where_season = ""
            if args.season_id and "season_id" in ci_cols:
                where_season = "AND ci.season_id = :season_id"
                params["season_id"] = args.season_id

            q = text(
                f"""
                SELECT
                  {select_sql}
                FROM operations.crop_indices ci
                LEFT JOIN operations.field_locations fl
                  ON fl.location_id = ci.location_id
                WHERE ci.location_id IN ({in_list})
                  AND ci.file_name IS NOT NULL
                  {where_season}
                ORDER BY ci.location_id, ci.file_name, ci.analysis_date, ci.id
                """
            )
            rows = db.execute(q, params).mappings().all()
            detailed_rows.extend([dict(r) for r in rows])

        # Convert prefixed keys to flat CSV headers
        headers: list[str] = []
        # Preserve a stable order: ci_*, then fl_*
        for c in select_ci:
            headers.append(f"ci_{c}")
        for c in select_fl:
            headers.append(f"fl_{c}")

        detailed_csv = out_dir / "crop_indices_by_location_ids.csv"
        _write_csv(detailed_csv, headers, detailed_rows)
        logger.info("Wrote detailed CSV: %s (rows=%d)", detailed_csv, len(detailed_rows))

        # Summary CSV
        summary: list[dict] = []
        for chunk in _chunks(location_ids, 500):
            params = {f"lid_{i}": v for i, v in enumerate(chunk)}
            in_list = ", ".join(f":lid_{i}" for i in range(len(chunk)))
            where_season = ""
            if args.season_id and "season_id" in ci_cols:
                where_season = "AND season_id = :season_id"
                params["season_id"] = args.season_id
            q2 = text(
                f"""
                SELECT
                  location_id,
                  file_name,
                  COUNT(*) AS row_count,
                  SUM(CASE WHEN lst_celsius IS NULL THEN 1 ELSE 0 END) AS lst_null_rows,
                  SUM(CASE WHEN vv_db IS NULL OR vh_db IS NULL OR vh_vv_ratio IS NULL THEN 1 ELSE 0 END) AS sar_any_null_rows
                FROM operations.crop_indices
                WHERE location_id IN ({in_list})
                  AND file_name IS NOT NULL
                  {where_season}
                GROUP BY location_id, file_name
                ORDER BY location_id, file_name
                """
            )
            rows2 = db.execute(q2, params).mappings().all()
            summary.extend([dict(r) for r in rows2])

        summary_csv = out_dir / "crop_indices_by_location_ids_summary.csv"
        _write_csv(
            summary_csv,
            ["location_id", "file_name", "row_count", "lst_null_rows", "sar_any_null_rows"],
            summary,
        )
        logger.info("Wrote summary CSV: %s (rows=%d)", summary_csv, len(summary))

    finally:
        db.close()


if __name__ == "__main__":
    main()

