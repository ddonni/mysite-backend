"""Database connection setup.

Reads the connection string from the DATABASE_URL environment variable so
the same code works against PostgreSQL (docker-compose / production) and
SQLite (quick local runs, CI tests) without any changes.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # 로컬 docker-compose 전용 호스트명("db") 같은 값으로 조용히 폴백하면,
    # 실제 환경변수 주입이 빠졌을 때 "DATABASE_URL이 없다"가 아니라
    # "db라는 호스트를 못 찾겠다"는 엉뚱하고 헷갈리는 에러가 남는다 —
    # 그래서 폴백 없이 여기서 바로 명확하게 실패시킴.
    raise RuntimeError("DATABASE_URL environment variable must be set")

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
