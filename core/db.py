from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import psycopg2.pool

from core.config import settings

DATABASE_URL = (
    f"postgresql://{settings.DB_USER}:{settings.DB_PASS}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()
DB_URL = {
    "dbname": settings.DB_NAME,
    "user": settings.DB_USER,
    "password": settings.DB_PASS,
    "host": settings.DB_HOST,
    "port": settings.DB_PORT
}

db_pool = psycopg2.pool.SimpleConnectionPool(minconn=1, maxconn=10, **DB_URL)

def get_connection():
    return db_pool.getconn()

def release_connection(conn):
    db_pool.putconn(conn)