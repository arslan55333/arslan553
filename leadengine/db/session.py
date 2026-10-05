"""Engine and session setup."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from leadengine.db.models import Base
from leadengine.log import get_logger

log = get_logger("db")


def make_engine(url: str, echo: bool = False) -> Engine:
    """Create an engine. For SQLite, creates the folder and turns on WAL + foreign keys."""
    if not url.startswith("sqlite"):
        return create_engine(url, echo=echo, pool_pre_ping=True)

    path = url.split("///", 1)[-1]
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, echo=echo, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return engine


def init_db(engine: Engine) -> list[str]:
    """Create missing tables, then add any new nullable columns to existing tables.

    This lightweight "add column" migration lets an older database keep working
    after an upgrade. Returns the list of ``table.column`` names that were added.
    """
    Base.metadata.create_all(engine)
    added: list[str] = []
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or not column.nullable:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {col_type}'))
                added.append(f"{table.name}.{column.name}")
                if column.index:
                    conn.execute(text(
                        f'CREATE INDEX IF NOT EXISTS ix_{table.name}_{column.name} ON {table.name} ("{column.name}")'
                    ))
    if added:
        log.info("database upgraded", extra={"data": {"added_columns": added}})
    return added


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
