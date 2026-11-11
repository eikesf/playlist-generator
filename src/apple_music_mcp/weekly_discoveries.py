"""Weekly Discoveries (guide §7.11): about 30 new tracks a week, chosen by Claude, checked by code.

Scheduled jobs cannot call an LLM (guide §10), so the curation runs in a **Claude Desktop scheduled
task** on the owner's Mac (risk assessment §2.5, docs/scheduled-tasks/weekly-discoveries.md):

1. ``context``: read-only. Everything Claude needs to choose: the main playlist's profile, mix, top
   artists and genres, dislikes, blacklist, earlier rejections, last week's batch, and candidate
   pools from the catalog (similar artists the owner does not know, unheard songs by known artists).
2. ``submit``: deterministic validation (catalog ids exist; not blacklisted, disliked, already
   heard, in the main playlist or its Arquivo, rejected before or repeated in the batch; size within
   range), then the batch replaces last week's tracks in "<name> · Weekly Discoveries" and is stored
   as a ``discoveries`` proposal (``applied``: the owner's scheduled task is the standing approval).
   Tracks the owner liked and that wait in an open promotion proposal stay in the playlist.

The main playlist is never touched here. Promotion into it is a separate, approved proposal.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .apple.errors import AppleMusicError
from .container import AppContainer
from .curation.known import KNOWN_ARTIST_NEW_TRACK, NEW_ARTIST
from .db.history import (
    ManagedPlaylistRepository,
    PlaylistEventRepository,
    ProposalRepository,
    ProposedTrack,
)
from .editing import TrackRef
from .models import Playlist, PlaylistEntry, Song
from .refresh import ARCHIVE_SUFFIX, find_playlist
from .sessions import track_info
from .text import primary_artist

WEEKLY_SUFFIX = " · Weekly Discoveries"
LEGACY_SUFFIX = " · Descobertas"


def size_range(target: int) -> tuple[int, int]:
    return max(1, round(target * 2 / 3)), max(1, round(target * 4 / 3))


class WeeklyDiscoveries:
    def __init__(self, app: AppContainer):
        self.app = app
        self.managed = ManagedPlaylistRepository(app.db)
        self.proposals = ProposalRepository(app.db)

    # ----------------------------------------------------------------- helpers
    def _enabled(self, playlist_id: str) -> dict[str, Any]:
        managed = self.managed.get(playlist_id)
        if managed is None:
            raise ValueError("not a managed playlist: adopt_playlist first, then turn on discoveries_enabled")
        if not managed.get("discoveries_enabled"):
            raise ValueError(
                "Weekly Discoveries is off for this playlist: update_managed_playlist with "
                "discoveries_enabled=true (only if the user asked for it)"
            )
        return managed

    async def weekly_playlist(self, managed: dict[str, Any], *, create: bool) -> Playlist | None:
        """The weekly playlist: the saved id, else one with the expected (or legacy) name."""
        saved = managed.get("discoveries_apple_playlist_id")
        if saved:
            try:
                return await self.app.library.get_playlist(saved)
            except AppleMusicError:
                pass
        name = managed["name"]
        found = await find_playlist(self.app, name + WEEKLY_SUFFIX) or await find_playlist(
            self.app, name + LEGACY_SUFFIX
        )
        if found is None and create:
            found = await self.app.library.create_playlist(
                name + WEEKLY_SUFFIX,
                f"New tracks for '{name}', picked every week. Love what you like: it moves up.",
            )
        if found is not None and found.id != saved:
            self.managed.upsert(
                managed["apple_playlist_id"], origin=managed["origin"], discoveries_apple_playlist_id=found.id
            )
        return found

    async def _ids_of(self, playlist_id: str | None) -> list[str]:
        if not playlist_id:
            return []
        return [TrackRef.of(e).id for e in await self.app.library.playlist_tracks(playlist_id)]

    def _held_for_promotion(self, managed: dict[str, Any]) -> set[str]:
        """Liked tracks waiting in an open promotion proposal: they stay in the weekly playlist."""
        held: set[str] = set()
        for p in self.proposals.find(kind="promotion", managed_playlist_id=managed["id"]):
            full = self.proposals.get(p["id"])
            for item in (full or {}).get("items", []):
                if item["decision"] != "rejected" and item["catalog_id"]:
                    held.add(str(item["catalog_id"]))
        return held

    # ----------------------------------------------------------------- context
    async def context(self, playlist_id: str | None) -> dict[str, Any]:
        if playlist_id is None:
            rows = [m for m in self.managed.list() if m.get("discoveries_enabled")]
            return {
                "playlists": [
                    {"playlist_id": m["apple_playlist_id"], "name": m["name"], "purpose": m["purpose"]}
                    for m in rows
                ],
                "next": "Call weekly_discoveries_context with each playlist_id.",
            }
        managed = self._enabled(playlist_id)
        entries = await self.app.library.playlist_tracks(playlist_id)
        artists = Counter(primary_artist(e.artist) for e in entries)
        names = {primary_artist(e.artist): e.artist for e in entries}
        top = [names[k] for k, _ in artists.most_common(10)]
        genres = await self._genres(entries)
        profile = self.app.profiles.get(managed.get("profile") or managed.get("purpose"))
        weekly = await self.weekly_playlist(managed, create=False)
        weekly_tracks = (
            [f"{e.artist} - {e.name}" for e in await self.app.library.playlist_tracks(weekly.id)]
            if weekly
            else []
        )
        discovery = self.app.discovery
        new_artists = (await discovery.discover(top[:5], per_artist=3, limit=60)).candidates
        deep_cuts = (await discovery.deep_cuts(top[:5], per_artist=3))["tracks"]
        in_main = {TrackRef.of(e).id for e in entries}

        def slim(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {
                    "id": r["id"],
                    "line": r.get("line") or f"{r['artist']} - {r['name']}",
                    "genres": r.get("genres"),
                }
                for r in rows
                if str(r["id"]) not in in_main
            ]

        target = self.app.settings.discoveries_size
        low, high = size_range(target)
        return {
            "playlist": {"playlist_id": playlist_id, "name": managed["name"], "purpose": managed["purpose"]},
            "profile": {"key": profile.key, "label": profile.label, "description": profile.description},
            "mix": {
                "known_artists_pct": managed.get("known_artists_pct"),
                "known_tracks_pct": managed.get("known_tracks_pct"),
                "note": "Discoveries are songs he never heard. The share by artists he already knows "
                "(deep cuts) should roughly follow known_artists_pct; the rest are new artists.",
            },
            "track_count": len(entries),
            "top_artists": top,
            "genres": genres,
            "recent_history": PlaylistEventRepository(self.app.db).history(managed["id"], 30),
            "dislikes": [
                {"line": f"{d['artist_name']} - {d['title']}", "reason": d["reason"]}
                for d in self.app.feedback.list(limit=50)
            ],
            "blacklist": [{"scope": b.scope, "label": b.label} for b in self.app.blacklist().items],
            "avoid": self.proposals.rejected_tracks(50),
            "current_weekly_playlist": {"name": weekly.name if weekly else None, "tracks": weekly_tracks},
            "candidates": {"new_artists": slim(new_artists), "deep_cuts": slim(deep_cuts)},
            "target_size": target,
            "size_range": [low, high],
            "next": (
                f"Choose about {target} tracks that fit the profile and the playlist's vibe (verify any "
                "extra picks with resolve_tracks), then call submit_weekly_discoveries with their ids."
            ),
        }

    async def _genres(self, entries: list[PlaylistEntry]) -> list[str]:
        ids = [e.catalog_id for e in entries if e.catalog_id]
        if not ids:
            return []
        try:
            songs = await self.app.catalog.get_songs(ids)
        except AppleMusicError:
            return []
        counts = Counter(g for s in songs for g in s.genres[:1])
        return [g for g, _ in counts.most_common(5)]

    # ------------------------------------------------------------------ submit
    async def submit(self, playlist_id: str, song_ids: list[str], notes: str | None = None) -> dict[str, Any]:
        managed = self._enabled(playlist_id)
        dropped: list[dict[str, str]] = []
        ids: list[str] = []
        for sid in (str(s).strip() for s in song_ids):
            if sid in ids:
                dropped.append({"song_id": sid, "reason": "duplicate"})
            elif sid:
                ids.append(sid)
        songs = {s.id: s for s in await self.app.catalog.get_songs(ids)} if ids else {}
        in_main = set(await self._ids_of(playlist_id))
        archive = await find_playlist(self.app, managed["name"] + ARCHIVE_SUFFIX)
        archived = set(
            await self._ids_of(managed.get("archive_apple_playlist_id") or (archive.id if archive else None))
        )
        rejected = {str(t["catalog_id"]) for t in self.proposals.rejected_tracks(500)}
        banned = self.app.blacklist()
        index = await self.app.discovery.index(ids)
        accepted: list[Song] = []
        for sid in ids:
            song = songs.get(sid)
            reason = None
            if song is None:
                reason = "not_in_catalog"
            elif sid in in_main:
                reason = "in_main_playlist"
            elif banned.blocked_reason(sid, song.artist, song.name):
                reason = "blacklisted"
            elif index and index.is_disliked(sid, song.artist, song.name):
                reason = "disliked"
            elif index and index.track_status(sid, song.artist, song.name) != "unheard":
                reason = "already_heard"
            elif sid in archived:
                reason = "archived_from_main"
            elif sid in rejected:
                reason = "rejected_before"
            if reason or song is None:
                dropped.append({"song_id": sid, "line": song.label if song else sid, "reason": reason or ""})
            else:
                accepted.append(song)
        target = self.app.settings.discoveries_size
        low, high = size_range(target)
        if not low <= len(accepted) <= high:
            raise ValueError(
                f"{len(accepted)} valid tracks; a weekly batch needs between {low} and {high} "
                f"(target {target}). Nothing was written. Dropped: {dropped}"
            )

        weekly = await self.weekly_playlist(managed, create=True)
        assert weekly is not None
        current = await self._ids_of(weekly.id)
        held = [i for i in current if i in self._held_for_promotion(managed)]
        batch = [s.id for s in accepted if s.id not in held]
        desired = held + batch
        warnings: list[str] = []
        if current:
            edit = await self.app.editor.sync(weekly.id, desired)
            if edit.status == "needs_decision":
                warnings.append(
                    "Apple refused replacing last week's tracks in place; the new batch was appended."
                )
        else:
            await self.app.library.add_tracks(weekly.id, desired)

        buckets = {}
        for s in accepted:
            buckets[s.id] = KNOWN_ARTIST_NEW_TRACK if index and index.knows_artist(s.artist) else NEW_ARTIST
        proposal_id = self.proposals.create(
            "discoveries",
            [
                ProposedTrack(track_info(s), action="add", bucket=buckets[s.id], decision="accepted")
                for s in accepted
            ],
            managed_playlist_id=managed["id"],
            status="applied",
            params={
                "playlist_id": playlist_id,
                "playlist_name": managed["name"],
                "weekly_playlist_id": weekly.id,
                "notes": notes,
                "dropped": dropped,
                "held_for_promotion": held,
            },
        )
        self.proposals.set_status(proposal_id, "applied")
        mix = Counter(buckets.values())
        return {
            "proposal_id": proposal_id,
            "weekly_playlist": weekly.to_dict(),
            "accepted": len(accepted),
            "dropped": dropped,
            "held_for_promotion": held,
            "mix": {"known_artist_new_track": mix[KNOWN_ARTIST_NEW_TRACK], "new_artist": mix[NEW_ARTIST]},
            "warnings": warnings,
        }
