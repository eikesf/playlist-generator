"""Catalog read operations (songs, artists), with the iTunes Search API as a search fallback."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from rapidfuzz import fuzz

from ..models import Artist, Song
from ..ports import SongSearcher
from ..text import normalize
from .errors import AppleMusicError, AuthError
from .transport import AmpTransport

MAX_SEARCH_LIMIT = 25
ARTIST_MATCH_THRESHOLD = 80
ARTIST_FILTER_THRESHOLD = 85


class CatalogService:
    def __init__(
        self,
        transport: AmpTransport,
        storefront: Callable[[], str],
        fallback: SongSearcher | None = None,
    ):
        self._amp = transport
        self._storefront = storefront
        self._fallback = fallback

    def _path(self, suffix: str) -> str:
        return f"/v1/catalog/{self._storefront()}{suffix}"

    async def _search(self, term: str, kind: str, limit: int) -> list[dict]:
        data = await self._amp.request(
            "GET", self._path("/search"), params={"term": term, "types": kind, "limit": _clamp(limit)}
        )
        return (((data or {}).get("results") or {}).get(kind) or {}).get("data") or []

    async def search_songs(self, term: str, limit: int = 10) -> list[Song]:
        try:
            return [Song.from_amp(i) for i in await self._search(term, "songs", limit)]
        except (AppleMusicError, httpx.HTTPError):
            if not self._fallback:
                raise
            return await self._fallback.search_songs(term, _clamp(limit))

    async def search_artists(self, term: str, limit: int = 5) -> list[Artist]:
        return [Artist.from_amp(i) for i in await self._search(term, "artists", limit)]

    async def artist_top_songs(self, artist_id: str, limit: int = 10) -> list[Song]:
        data = await self._amp.request(
            "GET",
            self._path(f"/artists/{artist_id}/view/top-songs"),
            params={"limit": max(1, min(limit, 20))},
        )
        return [Song.from_amp(i) for i in (data or {}).get("data", [])][:limit]

    async def get_songs(self, ids: list[str]) -> list[Song]:
        songs: list[Song] = []
        for i in range(0, len(ids), 300):
            data = await self._amp.request(
                "GET", self._path("/songs"), params={"ids": ",".join(ids[i : i + 300])}
            )
            songs.extend(Song.from_amp(item) for item in (data or {}).get("data", []))
        return songs

    async def find_artist(self, name: str) -> Artist | None:
        artists = await self.search_artists(name, limit=5)
        best = max(artists, key=lambda a: fuzz.ratio(normalize(a.name), normalize(name)), default=None)
        if best and fuzz.ratio(normalize(best.name), normalize(name)) >= ARTIST_MATCH_THRESHOLD:
            return best
        return None

    async def top_songs_for(self, artist_name: str, limit: int = 10) -> tuple[Artist | None, list[Song]]:
        """Top songs by artist name. Falls back to a song search filtered by artist when the artist
        page is unavailable. Auth errors propagate; other API errors trigger the fallback."""
        try:
            artist = await self.find_artist(artist_name)
            if artist:
                return artist, await self.artist_top_songs(artist.id, limit=limit)
        except AuthError:
            raise
        except AppleMusicError:
            pass
        songs = await self.search_songs(artist_name, limit=MAX_SEARCH_LIMIT)
        own = [
            s
            for s in songs
            if fuzz.token_set_ratio(normalize(artist_name), normalize(s.artist)) >= ARTIST_FILTER_THRESHOLD
        ]
        return None, own[:limit]


def _clamp(limit: int) -> int:
    return max(1, min(limit, MAX_SEARCH_LIMIT))
