"""Alembic environment. Migrations run programmatically through ``Database.upgrade()``."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Engine

from apple_music_mcp.db.schema import metadata

engine = context.config.attributes.get("engine")
if not isinstance(engine, Engine):  # pragma: no cover - only reachable from the alembic CLI
    raise RuntimeError("Run migrations through apple_music_mcp.db.Database.upgrade()")

with engine.begin() as connection:
    context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
