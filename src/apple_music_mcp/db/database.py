"""Opening the SQLite database and keeping its schema at the latest migration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event

from ..config import config_dir

DB_FILE = "apple-music-mcp.db"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")  # readers (dashboard) don't block the writer
    cursor.execute("PRAGMA busy_timeout = 5000")
    cursor.close()


def alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["engine"] = engine
    return cfg


class Database:
    """One SQLite file. ``open()`` creates it (0600) and applies pending migrations."""

    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / DB_FILE
        self.engine = create_engine(f"sqlite:///{self.path}")
        event.listen(self.engine, "connect", _sqlite_pragmas)

    @classmethod
    def open(cls, path: Path | None = None) -> Database:
        db = cls(path)
        db.upgrade()
        return db

    def upgrade(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch(mode=0o600)
        if not self.is_current():
            command.upgrade(alembic_config(self.engine), "head")

    def current_revision(self) -> str | None:
        with self.engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()

    def head_revision(self) -> str | None:
        return ScriptDirectory.from_config(alembic_config(self.engine)).get_current_head()

    def is_current(self) -> bool:
        return self.path.exists() and self.current_revision() == self.head_revision()

    def dispose(self) -> None:
        self.engine.dispose()
