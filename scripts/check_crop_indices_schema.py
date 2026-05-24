#!/usr/bin/env python3
"""
Verify operations.crop_indices table has all required columns.
Run from project root: python scripts/check_crop_indices_schema.py
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass

REQUIRED_COLUMNS = [
    "location_id", "grower_id", "variety_id",
    "grower_name", "variety_name",
    "village", "town", "district", "state", "country", "postcode",
    "polygon_area", "date_start", "date_end",
    "ndvi", "savi", "ndmi", "ndre", "gci", "psri", "msavi", "evi", "lai", "lst_celsius",
    "created_at", "id",
]

def main():
    from sqlalchemy import text
    try:
        from core.db import SessionLocal
        db = SessionLocal()
    except Exception as e:
        print(f"Cannot connect to DB: {e}")
        return 1
    try:
        r = db.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'operations' AND table_name = 'crop_indices'
            ORDER BY ordinal_position;
        """))
        existing = [row[0] for row in r.fetchall()]
    finally:
        db.close()

    print("Current columns in operations.crop_indices:")
    for c in existing:
        print(f"  {c}")
    print()

    missing = [c for c in REQUIRED_COLUMNS if c not in existing]
    if missing:
        print("MISSING COLUMNS:", missing)
        return 1
    print("All required columns exist.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
