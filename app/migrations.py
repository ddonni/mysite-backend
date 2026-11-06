"""Runs Alembic migrations against Postgres at startup.

This project has no separate deploy step for schema changes — main.py's
lifespan calls run_startup_migrations() every time the app boots, so
pulling the new code on the server and restarting the service is enough:
the new revision(s) under alembic/versions/ get applied before the app
starts serving traffic.

Before this, schema changes were hand-written idempotent ALTER TABLE
statements in this same file (see git history) — that didn't scale past a
handful of columns and, on SQLite (tests), was a no-op anyway since
`Base.metadata.create_all()` always builds the current schema from
scratch there. Alembic replaces that hand-rolled approach for Postgres;
SQLite still just uses `create_all()` (see main.py's lifespan) since a
throwaway test database has no history to migrate.

One-time wrinkle: the very first Alembic revision (_BASELINE_REVISION)
describes the schema exactly as it already existed in production (every
column every earlier hand-written migration had added). A database that
already has that schema but no `alembic_version` table yet — i.e.
production, the first time this runs — gets `alembic stamp` instead of
`alembic upgrade`: stamp just records "this database is at revision X"
without re-running X's `CREATE TABLE` calls (which would fail against
tables that already exist). Everything after that first stamp is a normal
`alembic upgrade head`.
"""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

# The first revision under alembic/versions/ — matches the schema that
# `Base.metadata.create_all()` + the old hand-written migrations already
# converged production to. Never change this constant after the first
# deploy; it only ever describes "day one" of Alembic history here.
_BASELINE_REVISION = "e7c3ca122668"

# Arbitrary key for a Postgres advisory lock that only this function uses.
# With more than one app instance booting at once (e.g. a Kubernetes
# Deployment with replicas: 2), each one would otherwise check "no
# alembic_version table yet" at the same moment and both try to create it —
# the loser crashes on startup and gets restarted. Holding this lock makes
# the instances take turns; whoever goes second finds the schema already at
# head and does nothing.
_MIGRATION_LOCK_KEY = 727274


def _alembic_config() -> Config:
    repo_root = Path(__file__).resolve().parent.parent
    return Config(str(repo_root / "alembic.ini"))


def run_startup_migrations(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        # SQLite (tests, quick local runs without docker-compose) always
        # gets the current schema straight from create_all() in main.py's
        # lifespan — a throwaway database has no history to migrate.
        return

    cfg = _alembic_config()
    # Session-level lock: held until unlocked or until this connection closes,
    # so a crash mid-migration releases it too. Alembic runs on its own
    # connections; the lock only has to keep other instances out.
    with engine.connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _MIGRATION_LOCK_KEY})
        try:
            # Checked after taking the lock: another instance may have just
            # finished migrating while this one was waiting.
            inspector = inspect(lock_conn)
            has_version_table = "alembic_version" in inspector.get_table_names()
            has_app_tables = "rooms" in inspector.get_table_names()
            # End the read transaction so this connection doesn't sit "idle in
            # transaction" while Alembic works; the advisory lock survives it.
            lock_conn.commit()

            if not has_version_table and has_app_tables:
                print(f"[migrations] pre-Alembic database detected - stamping it at {_BASELINE_REVISION} "
                      "instead of re-running that revision's CREATE TABLE calls")
                command.stamp(cfg, _BASELINE_REVISION)

            command.upgrade(cfg, "head")
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _MIGRATION_LOCK_KEY})
