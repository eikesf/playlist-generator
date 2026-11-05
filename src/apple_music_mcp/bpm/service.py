"""BPM lookup across providers with a persistent cache keyed by catalog id."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from ..config import StateStore
from .providers import BpmMatch, BpmProvider

CACHE_KEY = "bpm_cache"
MISS = "miss"


def align_octave(measured: float, estimate: float | None) -> float:
    """Databases sometimes report half/double time; snap to the octave closest to the estimate."""
    if not estimate:
        return measured
    options = [measured / 2, measured, measured * 2]
    return min(options, key=lambda v: abs(v - estimate))


class BpmService:
    def __init__(self, providers: list[BpmProvider], state: StateStore, concurrency: int = 3):
        self.providers = providers
        self._state = state
        self._sem = asyncio.Semaphore(concurrency)

    @property
    def available(self) -> list[str]:
        return [p.name for p in self.providers]

    async def lookup_one(self, track_id: str, artist: str, title: str) -> BpmMatch | None:
        cache = self._state.get(CACHE_KEY, {}) or {}
        hit = cache.get(track_id)
        if hit == MISS:
            return None
        if isinstance(hit, dict):
            return BpmMatch(**hit)
        match = None
        async with self._sem:
            for provider in self.providers:
                try:
                    match = await provider.lookup(artist, title)
                except Exception:  # noqa: BLE001 - one provider failing must not stop the others
                    match = None
                if match:
                    break
        cache = self._state.get(CACHE_KEY, {}) or {}
        cache[track_id] = asdict(match) if match else MISS
        self._state.set(CACHE_KEY, cache)
        return match

    async def lookup_many(self, tracks: list[dict[str, Any]]) -> dict[str, BpmMatch | None]:
        if not self.providers:
            return {}
        results = await asyncio.gather(
            *(self.lookup_one(str(t["id"]), t.get("artist", ""), t.get("title", "")) for t in tracks)
        )
        return {str(t["id"]): r for t, r in zip(tracks, results, strict=True)}
