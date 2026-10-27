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
# connect_timeout: libpq 기본값은 "무한정 기다림"이라, DB 쪽이 응답 없이 멈추면 요청도
# 같이 멈춤. 몇 초 안에 포기하고 에러를 내야 /health/db가 503으로 답할 수 있음.
if DATABASE_URL.startswith("postgresql"):
    connect_args["connect_timeout"] = 3

# pool_pre_ping: 풀에 있던 연결을 꺼낼 때 살아 있는지 먼저 확인함. PostgreSQL이
# 재시작되면(예: 새벽 자동 업데이트) 풀의 연결이 전부 끊기는데, 이게 없으면 그 뒤
# 첫 요청이 죽은 연결을 써서 500이 남(2026-10-18 EC2에서 실제로 겪음).
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a session, always closes it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
