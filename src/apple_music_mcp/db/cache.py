"""Response cache over the ``http_cache`` table (guide §4.1). Safe to empty at any time."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from .database import Database
from .schema import http_cache


def cache_key(namespace: str, *parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str)
    return f"{namespace}:{hashlib.sha256(raw.encode()).hexdigest()}"


class ResponseCache:
    def __init__(self, db: Database, clock: Any = None):
        self._db = db
        self._now = clock or (lambda: datetime.now(UTC))

    def get(self, key: str) -> Any | None:
        with self._db.engine.connect() as conn:
            row = conn.execute(
                select(http_cache.c.payload, http_cache.c.expires_at).where(http_cache.c.key == key)
            ).first()
        if row is None:
            return None
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
        return row.payload if expires > self._now() else None

    def set(self, key: str, payload: Any, ttl: timedelta) -> None:
        now = self._now()
        namespace = key.split(":", 1)[0]
        stmt = insert(http_cache).values(key=key, namespace=namespace, payload=payload, expires_at=now + ttl)
        stmt = stmt.on_conflict_do_update(
            index_elements=[http_cache.c.key],
            set_={"payload": stmt.excluded.payload, "expires_at": stmt.excluded.expires_at},
        )
        with self._db.engine.begin() as conn:
            conn.execute(stmt)
            conn.execute(delete(http_cache).where(http_cache.c.expires_at < now))

    def invalidate(self, namespace: str) -> int:
        with self._db.engine.begin() as conn:
            return conn.execute(delete(http_cache).where(http_cache.c.namespace == namespace)).rowcount

    def clear(self) -> int:
        with self._db.engine.begin() as conn:
            return conn.execute(delete(http_cache)).rowcount
