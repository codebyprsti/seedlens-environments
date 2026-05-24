#!/usr/bin/env python3
"""
Query time series from operations.crop_indices (e.g. by location_id or date range).

Output: JSON or CSV for dashboard or spreadsheet use.
Document: "date/time window corresponding to crop stage (e.g. 30 DAS, 60 DAS)".

Usage:
    python scripts/run_time_series_analysis.py --location-id LOC123 --format json
    python scripts/run_time_series_analysis.py --village "MyVillage" --date-start 2025-01-01 --date-end 2025-03-01 --format csv
"""

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Time-series crop indices from operations.crop_indices")
    parser.add_argument("--location-id", type=str, help="Filter by location_id")
    parser.add_argument("--grower-id", type=str, help="Filter by grower_id")
    parser.add_argument("--variety-id", type=str, help="Filter by variety_id")
    parser.add_argument("--village", type=str, help="Filter by village (partial match)")
    parser.add_argument("--date-start", type=str, help="Min date_end (YYYY-MM-DD)")
    parser.add_argument("--date-end", type=str, help="Max date_start (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=None, help="Max rows")
    parser.add_argument("--format", choices=["csv", "json"], default="json", help="Output format")
    args = parser.parse_args()

    try:
        from core.db import SessionLocal
    except ImportError as e:
        print(f"Error: {e}. Ensure project root and core.db are available.", file=sys.stderr)
        return 1

    session = SessionLocal()
    try:
        from crop_monitoring.time_series_builder import get_time_series
        from crop_monitoring.report_generator import to_csv, to_json

        rows = get_time_series(
            session,
            location_id=args.location_id or None,
            grower_id=args.grower_id or None,
            variety_id=args.variety_id or None,
            village=args.village or None,
            date_start=args.date_start or None,
            date_end=args.date_end or None,
            limit=args.limit,
        )
        if args.format == "csv":
            print(to_csv(rows))
        else:
            print(to_json(rows))
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
