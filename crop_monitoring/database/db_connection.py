"""
Database connection for crop_monitoring. Uses project core.db when available.
"""

from __future__ import annotations

from typing import Generator

try:
    from core.db import SessionLocal
    from sqlalchemy.orm import Session

    def get_db_session() -> Generator[Session, None, None]:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
except ImportError:
    SessionLocal = None
    Session = None

    def get_db_session():
        raise RuntimeError("core.db not available; run from project root or set DB connection")
