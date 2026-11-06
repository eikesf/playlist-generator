"""SQLite persistence: schema, migrations and small stores (docs/data-model.md)."""

from .database import DB_FILE, Database

__all__ = ["DB_FILE", "Database"]
