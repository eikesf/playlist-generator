"""Taste-aware discovery and familiarity annotation (known vs new, disliked)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .apple.catalog import CatalogService
from .apple.errors import AppleMusicError
from .apple.taste import TasteProfile, TasteService
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
    def __init__(self, catalog: CatalogService, taste: TasteService | None):
        self.catalog = catalog
        self.taste = taste

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
        per_count: dict[str, int] = {}
        seen_ids: set[str] = set()
        for song, similar, seed in pool:
            if song.id in seen_ids:
                continue
            seen_ids.add(song.id)
            if ratings.get(song.id) == -1:
                result.skipped_disliked += 1
                continue
            if exclude_known and profile and profile.is_known(song.id):
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
                    "artist_known_to_user": bool(profile and profile.knows_artist(song.artist)),
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

    async def annotate(self, report: ResolveReport) -> dict[str, Any]:
        """Add familiarity (known/new) and disliked flags to a resolve report."""
        data = report.to_dict()
        profile = await self._profile()
        ids = [r.song.id for r in report.resolutions if r.song]
        ratings = await self._ratings(ids)
        known = new = disliked = 0
        for row in data["tracks"]:
            song = row.get("song")
            if not song:
                continue
            if profile is not None:
                is_known = profile.is_known(song["id"])
                row["familiarity"] = "known" if is_known else "new"
                known += is_known
                new += not is_known
            if ratings.get(song["id"]) == -1:
                row["disliked"] = True
                disliked += 1
            elif ratings.get(song["id"]) == 1:
                row["loved"] = True
        summary = data["summary"]
        if profile is not None:
            summary["known"] = known
            summary["new"] = new
            total = known + new
            summary["known_ratio"] = round(known / total, 2) if total else None
        else:
            summary["familiarity"] = "unavailable (log in to measure known vs new)"
        if disliked:
            summary["disliked"] = disliked
            summary["note"] = "Tracks marked disliked were rated 'dislike' by the user: drop them."
        return data
