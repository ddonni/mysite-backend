"""One-time, idempotent fixup for the pre-rooms production database.

This project has no migration framework (no Alembic) — `Base.metadata.
create_all()` only creates tables that don't exist yet, it never alters
an existing one. Before rooms existed, `pages`/`records` had no room_id
column at all, so on a brand-new database create_all() already produces
the current schema (room_id NOT NULL, composite unique constraint) and
every check below is a no-op. On the one database that predates rooms
(production), this backfills a single "legacy" room to own the
pre-existing rows, then tightens the schema to match the models.

Safe to run on every startup: each step first checks whether it's
already done.
"""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from .crud import _CODE_ALPHABET, _CODE_LENGTH
import secrets


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def _unique_index_on(inspector, table: str, columns: list) -> str | None:
    for idx in inspector.get_indexes(table):
        if idx.get("unique") and idx.get("column_names") == columns:
            return idx["name"]
    for uc in inspector.get_unique_constraints(table):
        if uc.get("column_names") == columns:
            return uc["name"]
    return None


def run_startup_migrations(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        # SQLite (tests, local quick-start) always gets the current
        # schema straight from create_all() — nothing to backfill.
        return

    with engine.begin() as conn:
        inspector = inspect(conn)
        if "pages" not in inspector.get_table_names():
            return  # brand-new database; create_all() already built the current schema

        if "records" in inspector.get_table_names() and not _has_column(inspector, "records", "featured"):
            print("[migrations] adding records.featured column")
            conn.execute(text("ALTER TABLE records ADD COLUMN featured BOOLEAN NOT NULL DEFAULT false"))

        if not _has_column(inspector, "rooms", "theme"):
            print("[migrations] adding rooms.theme column")
            conn.execute(text("ALTER TABLE rooms ADD COLUMN theme VARCHAR NOT NULL DEFAULT 'wood'"))

        if not _has_column(inspector, "rooms", "name"):
            print("[migrations] adding rooms.name column")
            conn.execute(text("ALTER TABLE rooms ADD COLUMN name VARCHAR"))

        if not _has_column(inspector, "rooms", "google_sub"):
            print("[migrations] adding rooms.google_sub column")
            conn.execute(text("ALTER TABLE rooms ADD COLUMN google_sub VARCHAR"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_rooms_google_sub ON rooms (google_sub)"))

        if not _has_column(inspector, "rooms", "google_email"):
            print("[migrations] adding rooms.google_email column")
            conn.execute(text("ALTER TABLE rooms ADD COLUMN google_email VARCHAR"))

        pages_need_room = not _has_column(inspector, "pages", "room_id")
        records_need_room = "records" in inspector.get_table_names() and not _has_column(
            inspector, "records", "room_id"
        )
        if not pages_need_room and not records_need_room:
            return

        print("[migrations] backfilling legacy room for pre-rooms data")

        legacy_room_id = conn.execute(text("SELECT id FROM rooms ORDER BY id LIMIT 1")).scalar()
        if legacy_room_id is None:
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
            token = secrets.token_urlsafe(24)
            legacy_room_id = conn.execute(
                text("INSERT INTO rooms (code, token, created_at) VALUES (:code, :token, now()) RETURNING id"),
                {"code": code, "token": token},
            ).scalar()
            print(f"[migrations] created legacy room code={code} token={token} — "
                  "give this to whoever owned the pre-rooms data so they can claim it")

        if pages_need_room:
            conn.execute(text("ALTER TABLE pages ADD COLUMN room_id INTEGER"))
            conn.execute(text("UPDATE pages SET room_id = :rid WHERE room_id IS NULL"), {"rid": legacy_room_id})
            conn.execute(text("ALTER TABLE pages ALTER COLUMN room_id SET NOT NULL"))

            old_index = _unique_index_on(inspect(conn), "pages", ["page_number"])
            if old_index:
                conn.execute(text(f'DROP INDEX IF EXISTS "{old_index}"'))
                conn.execute(text(f'ALTER TABLE pages DROP CONSTRAINT IF EXISTS "{old_index}"'))
            conn.execute(text(
                "ALTER TABLE pages ADD CONSTRAINT uq_pages_room_page_number UNIQUE (room_id, page_number)"
            ))

        if records_need_room:
            conn.execute(text("ALTER TABLE records ADD COLUMN room_id INTEGER"))
            conn.execute(text("UPDATE records SET room_id = :rid WHERE room_id IS NULL"), {"rid": legacy_room_id})
            conn.execute(text("ALTER TABLE records ALTER COLUMN room_id SET NOT NULL"))

        print("[migrations] done")
