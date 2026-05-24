from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import psycopg2.pool
import logging

from core.config import settings
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

RAW_PASSWORD = settings.DB_PASS  # Use password from settings
ENCODED_PASSWORD = quote_plus(RAW_PASSWORD)  # For SQLAlchemy URL

DATABASE_URL = (
    f"postgresql://{settings.DB_USER}:{ENCODED_PASSWORD}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
)

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


Base = declarative_base()
DB_URL = {
    "dbname": settings.DB_NAME,
    "user": settings.DB_USER,
    "password": RAW_PASSWORD,
    "host": settings.DB_HOST,
    "port": settings.DB_PORT
}

_db_pool = None

def _get_pool():
    """Lazy initialization of connection pool"""
    global _db_pool
    if _db_pool is None:
        # Log connection parameters (without password) for debugging
        log_params = {k: v if k != 'password' else '*' * len(str(v)) for k, v in DB_URL.items()}
        logger.debug(f"Creating connection pool with parameters: {log_params}")
        try:
            _db_pool = psycopg2.pool.SimpleConnectionPool(minconn=1, maxconn=10, **DB_URL)
        except Exception as e:
            logger.error(f"Failed to create connection pool. Connection parameters:")
            logger.error(f"  Host: {DB_URL.get('host')}")
            logger.error(f"  Port: {DB_URL.get('port')}")
            logger.error(f"  Database: {DB_URL.get('dbname')}")
            logger.error(f"  User: {DB_URL.get('user')}")
            raise
    return _db_pool

def get_connection():
    return _get_pool().getconn()

def release_connection(conn):
    _get_pool().putconn(conn)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
