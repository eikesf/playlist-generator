"""Abstractions (ports) the domain logic depends on, so it never depends on HTTP details."""

from __future__ import annotations

from typing import Protocol

from .models import Song


class SongSearcher(Protocol):
    async def search_songs(self, term: str, limit: int = 10) -> list[Song]: ...
