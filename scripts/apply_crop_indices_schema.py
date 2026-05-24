#!/usr/bin/env python3
"""
Apply missing columns to operations.crop_indices (grower_name, variety_name).
Run once: python scripts/apply_crop_indices_schema.py
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

def main():
    from sqlalchemy import text
    try:
        from core.db import SessionLocal
        db = SessionLocal()
    except Exception as e:
        print(f"Cannot connect to DB: {e}")
        return 1
    try:
        for col in ["grower_name", "variety_name"]:
            try:
                db.execute(text(f"""
                    ALTER TABLE operations.crop_indices
                    ADD COLUMN IF NOT EXISTS {col} VARCHAR(200)
                """))
                db.commit()
                print(f"Added column (if not exist): {col}")
            except Exception as e:
                db.rollback()
                print(f"Error adding {col}: {e}")
                return 1
    finally:
        db.close()
    print("Schema update done.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
