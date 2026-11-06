"""Taste-aware discovery and familiarity annotation (known vs new, disliked)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .apple.catalog import CatalogService
from .apple.errors import AppleMusicError
from .apple.taste import TasteProfile, TasteService
from .curation.familiarity import BUCKETS, MixTargets, mix_check
from .curation.known import PARTLY_KNOWN, FamiliarityIndex, ListeningFacts
from .db.repositories import ListeningRepository, match_key
from .models import Artist, Song
from .resolver import ResolveReport
from .text import primary_artist


@dataclass
class DiscoveryResult:
    candidates: list[dict[str, Any]] = field(default_factory=list)
    skipped_known: int = 0
    skipped_disliked: int = 0
    seeds_not_found: list[str] = field(default_factory=list)
    taste_available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.candidates),
            "candidates": self.candidates,
            "skipped_known": self.skipped_known,
            "skipped_disliked": self.skipped_disliked,
            "seeds_not_found": self.seeds_not_found,
            "taste_available": self.taste_available,
            "note": "Candidates are NEW to the user (not in library / recently played). Check the vibe.",
        }


class DiscoveryService:
    def __init__(
        self,
        catalog: CatalogService,
        taste: TasteService | None,
        listening: ListeningRepository | None = None,
        min_plays: int = 10,
    ):
        self.catalog = catalog
        self.taste = taste
        self.listening = listening
        self.min_plays = min_plays

    async def index(self, song_ids: list[str] | None = None) -> FamiliarityIndex | None:
        """Familiarity for the given candidates (guide §7.4). None without a working login."""
        profile = await self._profile()
        if profile is None:
            return None
        ratings = await self._ratings(song_ids or [])
        facts = ListeningFacts(
            counters=self.listening.latest_by_match_key() if self.listening else {},
            loved_ids={i for i, r in ratings.items() if r == 1},
            disliked_ids={i for i, r in ratings.items() if r == -1},
            library_ids=set(profile.library_ids),
            library_keys={match_key(s["artist"], s["name"]) for s in profile.library_songs},
            recent_ids={s.id for s in profile.recent},
            recent_keys={match_key(s.artist, s.name) for s in profile.recent},
            known_artist_names=set(profile.artist_counts),
            min_plays=self.min_plays,
        )
        return FamiliarityIndex(facts)

    async def _profile(self) -> TasteProfile | None:
        if not self.taste:
            return None
        try:
            return await self.taste.profile()
        except AppleMusicError:  # includes AuthError: taste is an enhancement, never a blocker
            return None

    async def discover(
        self,
        seed_artists: list[str],
        per_artist: int = 3,
        similar_per_seed: int = 6,
        limit: int = 40,
        exclude_known: bool = True,
        exclude: list[str] | None = None,
    ) -> DiscoveryResult:
        """Top songs of artists similar to the seeds, excluding what the user already knows/dislikes."""
        profile = await self._profile()
        result = DiscoveryResult(taste_available=profile is not None)
        seeds = {primary_artist(s) for s in seed_artists}
        blocked = {primary_artist(a) for a in exclude or []}
        seen_artists: set[str] = set()
        pool: list[tuple[Song, Artist, str]] = []
        for seed in seed_artists:
            artist = await self.catalog.find_artist(seed)
            if not artist:
                result.seeds_not_found.append(seed)
                continue
            for similar in await self.catalog.similar_artists(artist.id, limit=similar_per_seed):
                key = primary_artist(similar.name)
                if key in seeds or key in seen_artists or key in blocked:
                    continue
                seen_artists.add(key)
                for song in await self.catalog.artist_top_songs(similar.id, limit=per_artist * 3):
                    pool.append((song, similar, seed))
        ratings = await self._ratings([s.id for s, _, _ in pool])
        index = await self.index([])
        per_count: dict[str, int] = {}
        seen_ids: set[str] = set()
        for song, similar, seed in pool:
            if song.id in seen_ids:
                continue
            seen_ids.add(song.id)
            if ratings.get(song.id) == -1:
                result.skipped_disliked += 1
                continue
            heard = index.track_status(song.id, song.artist, song.name) != "unheard" if index else False
            if exclude_known and heard:
                result.skipped_known += 1
                continue
            if per_count.get(similar.id, 0) >= per_artist:
                continue
            per_count[similar.id] = per_count.get(similar.id, 0) + 1
            result.candidates.append(
                {
                    **song.to_dict(),
                    "line": f"{song.artist} - {song.name}",
                    "similar_to": seed,
                    "artist_known_to_user": bool(index and index.knows_artist(song.artist)),
                }
            )
            if len(result.candidates) >= limit:
                break
        return result

    async def _ratings(self, ids: list[str]) -> dict[str, int]:
        if not self.taste or not ids:
            return {}
        try:
            return await self.taste.ratings(ids)
        except Exception:  # noqa: BLE001 - ratings are a filter, not a requirement
            return {}

    async def annotate(self, report: ResolveReport, targets: MixTargets | None = None) -> dict[str, Any]:
        """Tag each match with its familiarity bucket and loved/disliked; check the mix vs targets."""
        data = report.to_dict()
        ids = [r.song.id for r in report.resolutions if r.song]
        ratings = await self._ratings(ids)
        index = await self.index(ids) if ids else await self.index([])
        buckets = dict.fromkeys(BUCKETS, 0)
        partly = 0
        disliked = 0
        for row in data["tracks"]:
            song = row.get("song")
            if not song:
                continue
            if index is not None:
                bucket = index.bucket(song["id"], song["artist"], song["name"])
                row["familiarity"] = bucket
                plays = index.plays(song["artist"], song["name"])
                if plays is not None:
                    row["plays"] = plays
                if row["status"] == "matched":
                    if bucket == PARTLY_KNOWN:
                        partly += 1
                    else:
                        buckets[bucket] += 1
            if ratings.get(song["id"]) == -1 or (
                index and index.is_disliked(song["id"], song["artist"], song["name"])
            ):
                row["disliked"] = True
                disliked += 1
            elif ratings.get(song["id"]) == 1:
                row["loved"] = True
        summary = data["summary"]
        if index is not None:
            summary["familiarity"] = buckets
            summary["familiarity_definitions"] = index.definitions
            total = sum(buckets.values())
            known_artist_tracks = buckets["known_track"] + buckets["known_artist_new_track"]
            summary["known_tracks_pct"] = round(100 * buckets["known_track"] / total) if total else None
            summary["known_artists_pct"] = round(100 * known_artist_tracks / total) if total else None
            summary["known_tracks_within_known_artists_pct"] = (
                round(100 * buckets["known_track"] / known_artist_tracks) if known_artist_tracks else None
            )
            if partly:
                summary["partly_known"] = partly
                summary["partly_known_note"] = (
                    f"{partly} track(s) are only partly known (in the library or 1-{self.min_plays - 1} "
                    "plays, not loved): neither known nor new. Swap them unless the user wants them."
                )
            if targets is not None:
                summary["mix_check"] = mix_check(buckets, targets)
        else:
            summary["familiarity"] = "unavailable (log in to measure what is known)"
        if disliked:
            summary["disliked"] = disliked
            summary["note"] = "Tracks marked disliked were rated 'dislike' by the user: drop them."
        return data

    async def known_tracks(self, artists: list[str], limit: int = 50) -> dict[str, Any]:
        """Songs the user already knows by these artists; loved ones first (safest picks)."""
        profile = await self._profile()
        if profile is None:
            return {"available": False, "tracks": []}
        songs = profile.known_songs_by(artists)
        ratings = await self._ratings([s["id"] for s in songs])
        index = await self.index([s["id"] for s in songs])
        songs = [
            s
            for s in songs
            if ratings.get(s["id"]) != -1
            and (index is None or index.track_status(s["id"], s["artist"], s["name"]) == "known_track")
        ]
        songs.sort(key=lambda s: -ratings.get(s["id"], 0))
        rows = [
            {**s, "line": f"{s['artist']} - {s['name']}", "loved": ratings.get(s["id"]) == 1}
            for s in songs[:limit]
        ]
        return {"available": True, "count": len(rows), "tracks": rows}

    async def deep_cuts(self, artists: list[str], per_artist: int = 4) -> dict[str, Any]:
        """Songs by artists the user knows that the user has NOT heard yet (popular first)."""
        profile = await self._profile()
        pool: list[tuple[Song, str]] = []
        not_found = []
        for name in artists:
            artist = await self.catalog.find_artist(name)
            if not artist:
                not_found.append(name)
                continue
            for song in await self.catalog.artist_top_songs(artist.id, limit=20):
                pool.append((song, artist.name))
        ratings = await self._ratings([s.id for s, _ in pool])
        index = await self.index([])
        per: dict[str, int] = {}
        rows = []
        seen: set[str] = set()
        for song, artist_name in pool:
            heard = index is not None and index.track_status(song.id, song.artist, song.name) != "unheard"
            if song.id in seen or ratings.get(song.id) == -1 or heard:
                continue
            seen.add(song.id)
            if per.get(artist_name, 0) >= per_artist:
                continue
            per[artist_name] = per.get(artist_name, 0) + 1
            rows.append(
                {**song.to_dict(), "line": f"{song.artist} - {song.name}", "artist_page": artist_name}
            )
        return {
            "count": len(rows),
            "tracks": rows,
            "artists_not_found": not_found,
            "taste_available": bool(profile),
        }
