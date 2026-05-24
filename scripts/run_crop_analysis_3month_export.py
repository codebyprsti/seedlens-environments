"""
3-month crop indices export: one row per satellite observation, export to Excel.

Reuses the existing crop monitoring pipeline without modifying it.
For each day in [start_date, end_date], runs the pipeline and appends one row per
successful observation. Exports all rows to an Excel file.

Supports appending as a new sheet and resolving location_id, grower_id, variety_id
from master data when --resolve-ids is used (requires DB connection).
"""

from __future__ import annotations

import logging
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

# Project root for imports
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from crop_monitoring.pipeline import run_crop_analysis

# Excel sheet names: max 31 chars, no : \ / ? * [ ]
def _sanitize_sheet_name(name: str, max_len: int = 31) -> str:
    s = re.sub(r'[\:\\\/\?\*\[\]]', "_", name)
    s = s.strip() or "Sheet"
    return s[:max_len]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Default paths
DEFAULT_KML_PATH = Path(
    r"C:\Users\madan\Downloads\BIDARAHALLI ANAND C HORLAHALLI USRH24 CHANNAPPA.kml"
)
DEFAULT_OUTPUT_PATH = Path(r"C:\Users\madan\Downloads\crop_indices_3month_output.xlsx")

# Required Excel columns (exact order)
EXCEL_COLUMNS = [
    "id",
    "location_id",
    "grower_id",
    "variety_id",
    "polygon_id",
    "polygon_area",
    "date_start",
    "date_end",
    "analysis_date",
    "ndvi",
    "savi",
    "ndmi",
    "ndre",
    "gci",
    "psri",
    "msavi",
    "evi",
    "lai",
    "lst_c",
    "created_at",
    "village",
    "town",
    "district",
    "state",
    "country",
    "postcode",
    "lst_celsius",
    "grower_name",
    "variety_name",
    "vv_db",
    "vh_db",
    "vh_vv_ratio",
]


def _result_to_row(
    result: dict,
    date_start: str,
    date_end: str,
    row_id: int,
    created_at: str,
) -> dict:
    """Build a single output row from pipeline result and date range."""
    indices = result.get("indices") or {}
    metadata = result.get("metadata") or {}
    detected = result.get("detected_location") or {}

    def idx(k: str):
        v = indices.get(k)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else v

    village = detected.get("village") or metadata.get("village")
    grower = metadata.get("grower")
    variety = metadata.get("variety")

    return {
        "id": row_id,
        "location_id": result.get("location_id"),
        "grower_id": result.get("grower_id"),
        "variety_id": result.get("variety_id"),
        "polygon_id": None,
        "polygon_area": result.get("polygon_area_ha"),
        "date_start": date_start,
        "date_end": date_end,
        "analysis_date": date_end,
        "ndvi": idx("NDVI"),
        "savi": idx("SAVI"),
        "ndmi": idx("NDMI"),
        "ndre": idx("NDRE"),
        "gci": idx("GCI"),
        "psri": idx("PSRI"),
        "msavi": idx("MSAVI"),
        "evi": idx("EVI"),
        "lai": idx("LAI"),
        "lst_c": idx("LST_C"),
        "created_at": created_at,
        "village": village,
        "town": detected.get("town"),
        "district": detected.get("district"),
        "state": detected.get("state"),
        "country": detected.get("country"),
        "postcode": detected.get("postcode"),
        "lst_celsius": idx("LST_C"),
        "grower_name": grower,
        "variety_name": variety,
        "vv_db": idx("vv_db"),
        "vh_db": idx("vh_db"),
        "vh_vv_ratio": idx("vh_vv_ratio"),
    }


def run_3month_export(
    kml_path: str | Path,
    start_date: date | None = None,
    end_date: date | None = None,
    output_path: str | Path | None = None,
    *,
    sheet_name: str | None = None,
    append: bool = False,
    resolve_ids: bool = False,
) -> pd.DataFrame:
    """
    Load KML, run pipeline for each day in [start_date, end_date], collect rows, export to Excel.

    Uses last 90 days if start_date/end_date not provided.

    If append=True and output_path exists, writes the DataFrame as a new sheet (use sheet_name).
    If resolve_ids=True, passes a DB session to the pipeline so location_id, grower_id, variety_id
    are resolved from master data (requires core.db.SessionLocal).
    """
    kml_path = Path(kml_path)
    if not kml_path.exists():
        raise FileNotFoundError(f"KML not found: {kml_path}")

    end_date = end_date or date.today()
    start_date = start_date or (end_date - timedelta(days=90))
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    output_path = Path(output_path or DEFAULT_OUTPUT_PATH)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve IDs once from master data (one DB call) and reuse for all rows
    resolved_ids: dict | None = None
    if resolve_ids:
        try:
            from core.db import SessionLocal
            session = SessionLocal()
            logger.info("Resolving location_id, grower_id, variety_id from master data (one-time).")
            probe = run_crop_analysis(
                kml_path,
                start_date=start_date.isoformat(),
                end_date=start_date.isoformat(),
                store_in_db=False,
                db_session=session,
            )
            try:
                session.close()
            except Exception:
                pass
            resolved_ids = {
                "location_id": probe.get("location_id"),
                "grower_id": probe.get("grower_id"),
                "variety_id": probe.get("variety_id"),
            }
            logger.info(
                "Resolved IDs: location_id=%s, grower_id=%s, variety_id=%s",
                resolved_ids["location_id"],
                resolved_ids["grower_id"],
                resolved_ids["variety_id"],
            )
        except Exception as e:
            logger.warning("ID resolution failed (IDs will be empty): %s", e)

    created_at = date.today().isoformat()

    rows: list[dict] = []
    current = start_date
    day_count = (end_date - start_date).days + 1
    logger.info(
        "Processing KML %s from %s to %s (%d days); one observation per day.",
        kml_path.name,
        start_date.isoformat(),
        end_date.isoformat(),
        day_count,
    )

    for i in range(day_count):
        day_str = current.isoformat()
        try:
            result = run_crop_analysis(
                kml_path,
                start_date=day_str,
                end_date=day_str,
                store_in_db=False,
                db_session=None,
            )
            row = _result_to_row(result, day_str, day_str, row_id=len(rows) + 1, created_at=created_at)
            if resolved_ids:
                row["location_id"] = resolved_ids.get("location_id")
                row["grower_id"] = resolved_ids.get("grower_id")
                row["variety_id"] = resolved_ids.get("variety_id")
            rows.append(row)
            logger.info("Observation %s -> row %d", day_str, len(rows))
        except FileNotFoundError:
            logger.warning("KML not found for %s; skipping.", day_str)
        except Exception as e:
            logger.warning("Skip %s: %s", day_str, e, exc_info=False)
        current += timedelta(days=1)

    if not rows:
        logger.warning("No rows collected; writing empty DataFrame.")
        df = pd.DataFrame(columns=EXCEL_COLUMNS)
    else:
        df = pd.DataFrame(rows)
        # Ensure column order and fill missing columns
        for col in EXCEL_COLUMNS:
            if col not in df.columns:
                df[col] = None
        df = df[EXCEL_COLUMNS]

    # Sheet name for append: default from KML stem, sanitized; always truncate to Excel limit (31)
    effective_sheet = _sanitize_sheet_name(sheet_name if sheet_name is not None else kml_path.stem)

    if append and output_path.exists():
        with pd.ExcelWriter(output_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df.to_excel(writer, sheet_name=effective_sheet, index=False)
        logger.info("Appended %d rows to sheet %r in %s", len(df), effective_sheet, output_path)
    else:
        df.to_excel(output_path, index=False, engine="openpyxl")
        logger.info("Exported %d rows to %s", len(df), output_path)
    return df


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="3-month crop indices export to Excel")
    parser.add_argument(
        "kml",
        nargs="?",
        default=str(DEFAULT_KML_PATH),
        help="Path to KML file",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Start date (default: 90 days before end)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="End date (default: today)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=str(DEFAULT_OUTPUT_PATH),
        help="Output Excel path",
    )
    parser.add_argument(
        "--sheet",
        type=str,
        default=None,
        help="Sheet name for this export (used with --append)",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append as new sheet to existing Excel file instead of overwriting",
    )
    parser.add_argument(
        "--resolve-ids",
        action="store_true",
        help="Resolve location_id, grower_id, variety_id from master data (requires DB)",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None

    run_3month_export(
        kml_path=args.kml,
        start_date=start,
        end_date=end,
        output_path=args.output,
        sheet_name=args.sheet,
        append=args.append,
        resolve_ids=args.resolve_ids,
    )


if __name__ == "__main__":
    main()
