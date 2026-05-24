#!/usr/bin/env python3
"""
Test band_extractor: check whether we get band data from Sentinel Hub.

Usage:
    # With date range (required by the API):
    python scripts/test_band_extractor.py path/to/farm.kml --start 2025-02-01 --end 2025-02-28

    # With default date range (last 30 days):
    python scripts/test_band_extractor.py path/to/farm.kml

Requires: SH_CLIENT_ID, SH_CLIENT_SECRET in env (Copernicus Data Space).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.band_extractor import get_sentinel_bands, BAND_NAMES, last_n_days_range


def main():
    parser = argparse.ArgumentParser(description="Test band extractor: do we get band data?")
    parser.add_argument("kml_path", type=Path, help="Path to KML file with farm polygon")
    parser.add_argument("--start", type=str, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=str, help="End date YYYY-MM-DD")
    parser.add_argument("--last", type=int, default=5, help="Use last N days up to present (default: 5). Ignored if --start/--end set.")
    args = parser.parse_args()

    if not args.kml_path.exists():
        print(f"Error: KML not found: {args.kml_path}")
        sys.exit(1)

    if args.start and args.end:
        start_date, end_date = args.start, args.end
        print(f"Using date range: {start_date} to {end_date}\n")
    else:
        start_date, end_date = last_n_days_range(args.last)
        print(f"Using last {args.last} days up to present: {start_date} to {end_date}\n")

    print(f"KML: {args.kml_path}")
    print(f"Date range: {start_date} to {end_date}")
    print("Requesting bands:", ", ".join(BAND_NAMES))
    print()

    try:
        if args.start and args.end:
            bands = get_sentinel_bands(args.kml_path, start_date=args.start, end_date=args.end)
        else:
            bands = get_sentinel_bands(args.kml_path, last_days=args.last)
    except Exception as e:
        print("Result: NO BAND DATA")
        print(f"Error: {e}")
        sys.exit(1)

    # Report whether we got real data
    got_data = True
    for name in BAND_NAMES:
        arr = bands.get(name)
        if arr is None:
            print(f"  {name}: MISSING")
            got_data = False
            continue
        valid = ~(arr <= 0)  # typical no-data is 0 or negative
        n_valid = valid.sum()
        if n_valid == 0:
            print(f"  {name}: shape={arr.shape} but all values <= 0 (likely no data)")
            got_data = False
        else:
            print(f"  {name}: shape={arr.shape}  min={float(arr.min()):.4f}  max={float(arr.max()):.4f}  valid_pixels={int(n_valid)}")

    print()
    if got_data:
        print("Result: YES — we are getting band data (with date range).")
    else:
        print("Result: Arrays returned but no valid pixel values — try another date range or check cloud cover.")
    sys.exit(0 if got_data else 1)


if __name__ == "__main__":
    main()
