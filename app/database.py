"""Database connection setup.

Reads the connection string from the DATABASE_URL environment variable so
the same code works against PostgreSQL (docker-compose / production) and
SQLite (quick local runs, CI tests) without any changes.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://sketchbook:sketchbook@db:5432/sketchbook",
)

# SQLite needs this flag when used from a multi-threaded server like uvicorn;
# PostgreSQL doesn't, so only pass it when we're actually pointed at SQLite.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a session, always closes it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
