"""Weekly refresh of a playlist, built around one rule: never lose a track silently.

``plan`` (read-only for the main playlist):
  * rotation candidates = least-played tracks, ONLY when the Music app's play counts can tell them
    apart (tracks added recently and favorites are never candidates);
  * removal candidates = tracks the user disliked (rating or Music app);
  * new tracks from taste-aware discovery go to a separate staging playlist "<name> · Descobertas";
  * staging tracks the user loved / favorited / played a lot are promotion candidates.
``apply`` only touches what the user confirmed: removed tracks are first copied to
"<name> · Arquivo", promoted tracks are inserted at their best position and leave staging.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .apple.errors import AppleMusicError
from .container import AppContainer
from .editing import TrackRef
from .local_music import MusicAppReader, MusicAppUnavailable, TrackStats, track_key
from .models import Playlist, PlaylistEntry
from .operations import insert_into_playlist
from .text import primary_artist

STAGING_SUFFIX = " · Descobertas"
ARCHIVE_SUFFIX = " · Arquivo"
PLANS_KEY = "refresh_plans"
MIN_DAYS_IN_PLAYLIST = 14
PROMOTE_MIN_PLAYS = 3


@dataclass
class RefreshSettings:
    rotate_fraction: float = 0.2
    min_days_in_playlist: int = MIN_DAYS_IN_PLAYLIST
    new_tracks: int | None = None  # default: rotation size, at least 5
    seed_artists: list[str] | None = None


def _label(e: PlaylistEntry) -> str:
    return f"{e.artist} - {e.name}"


class RefreshService:
    def __init__(self, app: AppContainer, music_app: MusicAppReader | None = None):
        self.app = app
        self.music_app = music_app or MusicAppReader()

    # ----------------------------------------------------------------- helpers
    async def _find_or_create(self, name: str, description: str) -> tuple[Playlist, bool]:
        for pl in await self.app.library.list_playlists(limit=500):
            if pl.name == name:
                return pl, False
        return await self.app.library.create_playlist(name, description), True

    async def _stats(self, playlist: Playlist) -> tuple[dict[tuple[str, str], TrackStats], str | None]:
        try:
            stats = await self.music_app.playlist_stats(playlist.name)
        except MusicAppUnavailable as exc:
            return {}, str(exc)
        return {s.key: s for s in stats}, None

    async def _ratings(self, entries: list[PlaylistEntry]) -> dict[str, int]:
        ids = [e.catalog_id for e in entries if e.catalog_id]
        try:
            return await self.app.taste.ratings(ids)
        except AppleMusicError:
            return {}

    # -------------------------------------------------------------------- plan
    async def plan(self, playlist_id: str, settings: RefreshSettings | None = None) -> dict[str, Any]:
        settings = settings or RefreshSettings()
        playlist = await self.app.library.get_playlist(playlist_id)
        entries = await self.app.library.playlist_tracks(playlist_id)
        stats, stats_error = await self._stats(playlist)
        ratings = await self._ratings(entries)
        notes: list[str] = []

        def st(e: PlaylistEntry) -> TrackStats | None:
            return stats.get(track_key(e.artist, e.name))

        removal = [
            {"id": TrackRef.of(e).id, "track": _label(e), "reason": "disliked"}
            for e in entries
            if ratings.get(e.catalog_id or "") == -1 or (st(e) and st(e).disliked)
        ]
        removal_ids = {r["id"] for r in removal}

        rotation: list[dict[str, Any]] = []
        n_rotate = max(1, round(len(entries) * settings.rotate_fraction)) if entries else 0
        if stats:
            cutoff = datetime.now() - timedelta(days=settings.min_days_in_playlist)
            eligible = [
                (e, st(e))
                for e in entries
                if st(e)
                and TrackRef.of(e).id not in removal_ids
                and not st(e).favorited
                and ratings.get(e.catalog_id or "") != 1
                and (st(e).date_added is None or st(e).date_added <= cutoff)
            ]
            plays = sorted({s.played for _, s in eligible})
            if len(plays) > 1:  # play counts actually distinguish tracks
                eligible.sort(key=lambda es: (es[1].played, -es[1].skipped))
                threshold = eligible[min(n_rotate, len(eligible)) - 1][1].played
                rotation = [
                    {"id": TrackRef.of(e).id, "track": _label(e), "plays": s.played, "skips": s.skipped}
                    for e, s in eligible
                    if s.played <= threshold
                ][:n_rotate]
            else:
                notes.append("Play counts do not tell tracks apart yet: no rotation suggested.")
        else:
            notes.append(f"No play data ({stats_error}). Only dislikes and staging are used.")

        staging, created = await self._find_or_create(
            playlist.name + STAGING_SUFFIX,
            f"Novidades para '{playlist.name}'. Curta o que gostar: sobe para a principal.",
        )
        staging_entries = [] if created else await self.app.library.playlist_tracks(staging.id)
        promotions = await self._promotion_candidates(staging, staging_entries)

        new_count = settings.new_tracks or max(5, len(rotation))
        seeds = settings.seed_artists or self._top_artists(entries, 5)
        in_main = {TrackRef.of(e).id for e in entries}
        in_staging = {TrackRef.of(e).id for e in staging_entries}
        archive_ids = await self._archive_ids(playlist)
        discovered = await self.app.discovery.discover(seeds, per_artist=2, limit=new_count * 3)
        fresh = [
            c
            for c in discovered.candidates
            if c["id"] not in in_main and c["id"] not in in_staging and c["id"] not in archive_ids
        ][:new_count]
        if fresh:
            await self.app.library.add_tracks(staging.id, [c["id"] for c in fresh])

        plan = {
            "playlist": playlist.to_dict(),
            "tracks_in_playlist": len(entries),
            "play_data": bool(stats),
            "rotation_candidates": rotation,
            "removal_candidates": removal,
            "staging_playlist": staging.to_dict(),
            "added_to_staging": [
                {"id": c["id"], "track": c["line"], "similar_to": c["similar_to"]} for c in fresh
            ],
            "promotion_candidates": promotions,
            "main_playlist_changed": False,
            "notes": notes
            + [
                "Nothing was removed from the main playlist. Ask the user which candidates to remove "
                "(they are archived first) and which staging tracks to promote."
            ],
            "created_at": time.time(),
        }
        plans = self.app.state.get(PLANS_KEY, {}) or {}
        plans[playlist_id] = {k: plan[k] for k in ("rotation_candidates", "removal_candidates", "created_at")}
        self.app.state.set(PLANS_KEY, plans)
        return plan

    def _top_artists(self, entries: list[PlaylistEntry], n: int) -> list[str]:
        counts: dict[str, tuple[int, str]] = {}
        for e in entries:
            key = primary_artist(e.artist)
            count, name = counts.get(key, (0, e.artist))
            counts[key] = (count + 1, name)
        return [name for _, name in sorted(counts.values(), key=lambda c: -c[0])[:n]]

    async def _archive_ids(self, playlist: Playlist) -> set[str]:
        for pl in await self.app.library.list_playlists(limit=500):
            if pl.name == playlist.name + ARCHIVE_SUFFIX:
                return {TrackRef.of(e).id for e in await self.app.library.playlist_tracks(pl.id)}
        return set()

    async def _promotion_candidates(
        self, staging: Playlist, entries: list[PlaylistEntry]
    ) -> list[dict[str, Any]]:
        if not entries:
            return []
        stats, _ = await self._stats(staging)
        ratings = await self._ratings(entries)
        out = []
        for e in entries:
            s = stats.get(track_key(e.artist, e.name))
            reasons = []
            if ratings.get(e.catalog_id or "") == 1 or (s and s.favorited):
                reasons.append("loved")
            if s and s.played >= PROMOTE_MIN_PLAYS:
                reasons.append(f"played {s.played}x")
            if reasons:
                out.append({"id": TrackRef.of(e).id, "track": _label(e), "why": ", ".join(reasons)})
        return out

    # ------------------------------------------------------------------- apply
    async def apply(
        self,
        playlist_id: str,
        purpose: str,
        remove_ids: list[str] | None = None,
        promote: list[dict[str, Any]] | None = None,
        current_estimates: list[dict[str, Any]] | None = None,
        allow_rebuild: bool = False,
    ) -> dict[str, Any]:
        """Apply ONLY what the user confirmed. Removed tracks are archived before removal."""
        remove_ids = [str(i) for i in remove_ids or []]
        promote = promote or []
        playlist = await self.app.library.get_playlist(playlist_id)
        entries = await self.app.library.playlist_tracks(playlist_id)
        by_id = {TrackRef.of(e).id: e for e in entries}
        unknown = [i for i in remove_ids if i not in by_id]
        result: dict[str, Any] = {"playlist": playlist.to_dict(), "warnings": []}
        if unknown:
            result["warnings"].append(f"Not in the playlist, ignored: {unknown}")
        to_remove = [i for i in remove_ids if i in by_id]

        if to_remove:
            archive, _ = await self._find_or_create(
                playlist.name + ARCHIVE_SUFFIX, f"Faixas que saíram de '{playlist.name}'. Nada se perde."
            )
            archived = {TrackRef.of(e).id for e in await self.app.library.playlist_tracks(archive.id)}
            refs = [TrackRef.parse(i) for i in to_remove if i not in archived]
            for kind in ("songs", "library-songs"):
                ids = [r.id for r in refs if r.kind == kind]
                if ids:
                    await self.app.library.add_tracks(archive.id, ids, kind)
            check = {TrackRef.of(e).id for e in await self.app.library.playlist_tracks(archive.id)}
            if not set(to_remove) <= check:
                raise AppleMusicError(
                    "Could not confirm the archive copy; nothing was removed from the playlist."
                )
            desired = [i for i in by_id if i not in set(to_remove)]
            edit = await self.app.editor.sync(playlist_id, desired, allow_rebuild)
            result["removed"] = {"tracks": [_label(by_id[i]) for i in to_remove], "edit": edit.to_dict()}
            result["archive_playlist"] = archive.to_dict()
            if edit.status == "needs_decision":
                result["warnings"].append(
                    "Removal needs a decision (see removed.edit.decision); archive copy is safe."
                )
                return result
            playlist_id = edit.playlist.id  # may change only after a confirmed rebuild

        if promote:
            inserted = await insert_into_playlist(
                self.app, playlist_id, purpose, promote, current_estimates, allow_rebuild
            )
            result["promoted"] = inserted
            staging = None
            for pl in await self.app.library.list_playlists(limit=500):
                if pl.name == playlist.name + STAGING_SUFFIX:
                    staging = pl
            if staging and (inserted.get("edit") or {}).get("status") == "applied":
                keep = [
                    TrackRef.of(e).id
                    for e in await self.app.library.playlist_tracks(staging.id)
                    if TrackRef.of(e).id not in {str(p["id"]) for p in promote}
                ]
                try:
                    cleanup = await self.app.editor.sync(staging.id, keep)
                    result["staging_cleanup"] = cleanup.status
                except AppleMusicError as exc:
                    result["warnings"].append(f"Promoted tracks are still in the staging playlist: {exc}")
        return result
