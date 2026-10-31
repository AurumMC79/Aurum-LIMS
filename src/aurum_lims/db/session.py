"""Database connection, sessions, and schema migrations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):
        # Let SQLAlchemy (not the sqlite3 module) control transactions, so the
        # "begin" hook below can take the write lock up front.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 10000")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection):
        # BEGIN IMMEDIATE serialises writers, which keeps the audit hash chain
        # consistent even if two copies of the app share one database file.
        connection.exec_driver_sql("BEGIN IMMEDIATE")


class Database:
    """Owns the SQLAlchemy engine and hands out sessions."""

    def __init__(self, url: str):
        self.url = url
        self.engine = create_engine(url)
        if self.engine.dialect.name == "sqlite":
            if url.startswith("sqlite:///") and ":memory:" not in url:
                Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
            _configure_sqlite(self.engine)
        self._sessionmaker = sessionmaker(self.engine, expire_on_commit=False)

    def upgrade(self) -> None:
        """Bring the schema up to the latest migration."""
        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        cfg.set_main_option("sqlalchemy.url", self.url.replace("%", "%%"))
        with self.engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Unit of work: commits on success, rolls back on any exception."""
        session = self._sessionmaker()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
        self.checkpoint()  # nearly free when nothing new was written

    def checkpoint(self) -> None:
        """Copy saved changes from the write-ahead log into the database file straight away (SQLite).

        Normally SQLite does this every ~1000 pages, so small saves can sit in the -wal file for a long time.
        Doing it after each save keeps the main file complete on disk at all times: other programs (backups,
        the bug-report reader) see the latest data, and nothing depends on the -wal file surviving.
        PASSIVE never waits for or blocks other connections."""
        if self.engine.dialect.name != "sqlite":
            return
        raw = self.engine.raw_connection()
        try:
            raw.cursor().execute("PRAGMA wal_checkpoint(PASSIVE)")
        finally:
            raw.close()

    def dispose(self) -> None:
        self.engine.dispose()
