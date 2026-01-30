"""
Database connection module for cumulative inspection data
Reuses the existing database connection from core.db
"""
from core.db import get_connection, release_connection, engine
from sqlalchemy.orm import Session
from core.db import get_db as get_db_session

__all__ = ['get_connection', 'release_connection', 'engine', 'get_db_session']

