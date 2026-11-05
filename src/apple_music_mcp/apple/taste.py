"""The user's real taste from Apple Music: library, recently played, heavy rotation, ratings.

Used to tell "known" from "new" tracks (so the known/new ratio is measured, not guessed), to avoid
disliked songs and to seed discovery. Results are cached in the local state file for a few hours.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..config import StateStore
from ..models import Song
from ..text import artist_names
from .errors import AppleMusicError, AuthError
from .transport import AmpTransport

CACHE_KEY = "taste_cache"
CACHE_TTL_SECONDS = 6 * 3600
MAX_LIBRARY_SONGS = 5000
RATINGS_CHUNK = 100
RECENT_PAGE_SIZE = 10  # values above 10 are rejected (community reports)
RECENT_MAX_ITEMS = 50


@dataclass
class TasteProfile:
    library_ids: set[str] = field(default_factory=set)
    library_songs: list[dict[str, str]] = field(default_factory=list)  # id, name, artist
    recent: list[Song] = field(default_factory=list)
    heavy_rotation_artists: list[str] = field(default_factory=list)
    artist_counts: dict[str, int] = field(default_factory=dict)  # display name -> weight
    fetched_at: float = field(default_factory=time.time)

    @property
    def known_ids(self) -> set[str]:
        return self.library_ids | {s.id for s in self.recent}

    def is_known(self, song_id: str) -> bool:
        return song_id in self.known_ids

    def knows_artist(self, artist: str) -> bool:
        """True if any credited artist (main or featured) is one the user listens to."""
        known = self._known_artist_keys()
        return any(name in known for name in artist_names(artist))

    def _known_artist_keys(self) -> set[str]:
        keys: set[str] = set()
        for a in self.artist_counts:
            keys.update(artist_names(a))
        return keys

    def bucket(self, song_id: str, artist: str) -> str:
        if self.is_known(song_id):
            return "known_track"
        return "known_artist_new_track" if self.knows_artist(artist) else "new_artist"

    def known_songs_by(self, artists: list[str]) -> list[dict[str, str]]:
        """Songs the user knows (library + recently played) credited to any of ``artists``."""
        wanted = {n for a in artists for n in artist_names(a)}
        songs = self.library_songs + [{"id": s.id, "name": s.name, "artist": s.artist} for s in self.recent]
        seen: set[str] = set()
        out = []
        for song in songs:
            if song["id"] in seen or not set(artist_names(song["artist"])) & wanted:
                continue
            seen.add(song["id"])
            out.append(song)
        return out

    def top_artists(self, n: int = 20) -> list[dict[str, Any]]:
        return [{"artist": a, "weight": w} for a, w in Counter(self.artist_counts).most_common(n)]

    def to_cache(self) -> dict[str, Any]:
        return {
            "library_ids": sorted(self.library_ids),
            "library_songs": self.library_songs,
            "recent": [s.to_dict() for s in self.recent],
            "heavy_rotation_artists": self.heavy_rotation_artists,
            "artist_counts": self.artist_counts,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_cache(cls, data: dict[str, Any]) -> TasteProfile:
        fields = {
            "id",
            "name",
            "artist",
            "album",
            "duration_ms",
            "genres",
            "release_date",
            "url",
            "isrc",
            "explicit",
            "source",
        }
        return cls(
            library_ids=set(data.get("library_ids", [])),
            library_songs=list(data.get("library_songs", [])),
            recent=[Song(**{k: v for k, v in s.items() if k in fields}) for s in data.get("recent", [])],
            heavy_rotation_artists=list(data.get("heavy_rotation_artists", [])),
            artist_counts=dict(data.get("artist_counts", {})),
            fetched_at=float(data.get("fetched_at", 0)),
        )


class TasteService:
    def __init__(self, transport: AmpTransport, state: StateStore):
        self._amp = transport
        self._state = state
        self._profile: TasteProfile | None = None

    async def profile(self, refresh: bool = False) -> TasteProfile:
        if not refresh:
            if self._profile and time.time() - self._profile.fetched_at < CACHE_TTL_SECONDS:
                return self._profile
            cached = self._state.get(CACHE_KEY)
            if cached and time.time() - float(cached.get("fetched_at", 0)) < CACHE_TTL_SECONDS:
                self._profile = TasteProfile.from_cache(cached)
                return self._profile
        self._profile = await self._fetch()
        self._state.set(CACHE_KEY, self._profile.to_cache())
        return self._profile

    async def _fetch(self) -> TasteProfile:
        profile = TasteProfile()
        counts: Counter[str] = Counter()
        async for item in self._amp.paginate(
            "/v1/me/library/songs", params={"limit": 100}, user=True, limit=MAX_LIBRARY_SONGS
        ):
            attrs = item.get("attributes") or {}
            catalog_id = (attrs.get("playParams") or {}).get("catalogId")
            if catalog_id:
                profile.library_ids.add(str(catalog_id))
                profile.library_songs.append(
                    {
                        "id": str(catalog_id),
                        "name": attrs.get("name", ""),
                        "artist": attrs.get("artistName", ""),
                    }
                )
            if attrs.get("artistName"):
                counts[attrs["artistName"]] += 1
        profile.recent = await self._optional(self._recent)
        for song in profile.recent:
            counts[song.artist] += 3  # recent listening weighs more than an old library add
        profile.heavy_rotation_artists = await self._optional(self._heavy_rotation)
        for artist in profile.heavy_rotation_artists:
            counts[artist] += 5
        profile.artist_counts = dict(counts)
        return profile

    async def _optional(self, fn: Any) -> list:
        """Recent/heavy-rotation are nice-to-have: some accounts return errors or nothing."""
        try:
            return await fn()
        except AuthError:
            raise
        except AppleMusicError:
            return []

    async def _recent(self) -> list[Song]:
        return (await self.recent_tracks())[0]

    async def recent_tracks(self) -> tuple[list[Song], int]:
        """Recently played songs (newest first) and how many non-song items were skipped.

        Apple caps the page size at 10 and keeps only the last ~30-50 items, so page with offsets.
        """
        songs: list[Song] = []
        other = 0
        for offset in range(0, RECENT_MAX_ITEMS, RECENT_PAGE_SIZE):
            data = await self._amp.request(
                "GET",
                "/v1/me/recent/played/tracks",
                params={"limit": RECENT_PAGE_SIZE, "offset": offset},
                user=True,
            )
            items = (data or {}).get("data") or []
            songs += [Song.from_amp(i) for i in items if i.get("type") == "songs"]
            other += sum(1 for i in items if i.get("type") != "songs")
            if len(items) < RECENT_PAGE_SIZE:
                break
        return songs, other

    async def _heavy_rotation(self) -> list[str]:
        data = await self._amp.request(
            "GET", "/v1/me/history/heavy-rotation", params={"limit": 10}, user=True
        )
        names = []
        for item in (data or {}).get("data") or []:
            artist = (item.get("attributes") or {}).get("artistName")
            if artist and artist not in names:
                names.append(artist)
        return names

    async def ratings(self, song_ids: list[str]) -> dict[str, int]:
        """Catalog song id -> 1 (loved) / -1 (disliked); unrated songs are absent."""
        result: dict[str, int] = {}
        ids = [i for i in dict.fromkeys(song_ids) if i and not i.startswith("i.")]
        for k in range(0, len(ids), RATINGS_CHUNK):
            try:
                data = await self._amp.request(
                    "GET",
                    "/v1/me/ratings/songs",
                    params={"ids": ",".join(ids[k : k + RATINGS_CHUNK])},
                    user=True,
                )
            except AuthError:
                raise
            except AppleMusicError as exc:
                if exc.status == 404:  # none of these songs is rated
                    continue
                raise
            for item in (data or {}).get("data") or []:
                value = (item.get("attributes") or {}).get("value")
                if value in (1, -1):
                    result[str(item.get("id"))] = int(value)
        return result
