"""Abstractions (ports) the domain logic depends on, so it never depends on HTTP details."""

from __future__ import annotations

from typing import Any, Protocol

from .models import Song


class KeyValueStore(Protocol):
    """Small non-secret state (remembered strategies, caches). Backed by the ``setting`` table."""

    def get(self, key: str, default: Any = None) -> Any: ...

    def set(self, key: str, value: Any) -> None: ...


class SongSearcher(Protocol):
    async def search_songs(self, term: str, limit: int = 10) -> list[Song]: ...
