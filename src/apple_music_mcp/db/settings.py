"""Key-value store over the ``setting`` table, replacing ``state.json`` (docs/data-model.md §2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from .database import Database
from .schema import setting

IMPORTED_SUFFIX = ".imported"


class SettingsStore:
    """Same interface as the old ``StateStore`` (``get``/``set``), backed by SQLite."""

    def __init__(self, db: Database):
        self._db = db

    def get(self, key: str, default: Any = None) -> Any:
        with self._db.engine.connect() as conn:
            row = conn.execute(select(setting.c.value).where(setting.c.key == key)).first()
        return default if row is None or row.value is None else row.value

    def set(self, key: str, value: Any) -> None:
        stmt = insert(setting).values(key=key, value=value, updated_at=datetime.now(UTC))
        stmt = stmt.on_conflict_do_update(
            index_elements=[setting.c.key],
            set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
        )
        with self._db.engine.begin() as conn:
            conn.execute(stmt)

    def keys(self) -> list[str]:
        with self._db.engine.connect() as conn:
            return list(conn.execute(select(setting.c.key).order_by(setting.c.key)).scalars())


def import_state_file(store: SettingsStore, path: Path) -> list[str]:
    """Copy a legacy ``state.json`` into the database once, then rename it (kept as a backup).

    Keys already present in the database win: the file is older. Returns the imported keys.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    existing = set(store.keys())
    imported = []
    for key, value in data.items():
        if key not in existing:
            store.set(key, value)
            imported.append(key)
    path.rename(path.with_name(path.name + IMPORTED_SUFFIX))
    return imported
