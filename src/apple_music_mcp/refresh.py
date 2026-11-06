"""Weekly refresh of a playlist, built around one rule: never lose a track silently (ADR 0010).

``build_plan`` is **read-only** for Apple Music: it reads the playlist, the daily Music app snapshots
and the catalog, and stores a ``refresh`` proposal (guide §7.10):
  * rotation: tracks often skipped or not played in the last weeks, only with enough history
    (``min_history_days``) and only while the playlist itself was played. Favorites, loved tracks
    and recent additions are never candidates;
  * removal of tracks the owner disliked (rating or Music app);
  * one replacement per removal (``replaces_item_id``), from the same familiarity bucket so the
    playlist's mix holds, never from the blacklist, dislikes, the playlist, its Arquivo or tracks
    the owner rejected before.
``apply_refresh`` only executes items the owner accepted. Removed tracks are first copied to
"<name> · Arquivo", and the copy is verified before anything is removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from typing import Any

from .apple.errors import AppleMusicError
from .container import AppContainer
from .curation.known import KNOWN_ARTIST_NEW_TRACK, KNOWN_TRACK, NEW_ARTIST, PARTLY_KNOWN
from .curation.listening import Delta, ListeningWindow
from .db.history import ManagedPlaylistRepository, PlaylistEventRepository, ProposalRepository, ProposedTrack
from .db.repositories import ListeningRepository, TrackInfo, match_key
from .editing import TrackRef
from .local_music import MusicAppReader, MusicAppUnavailable, TrackStats, track_key
from .models import Playlist, PlaylistEntry
from .text import primary_artist

ARCHIVE_SUFFIX = " · Arquivo"
MIN_DAYS_IN_PLAYLIST = 14


@dataclass
class RefreshSettings:
    rotate_fraction: float = 0.2
    min_days_in_playlist: int = MIN_DAYS_IN_PLAYLIST
    window_days: int = 42
    min_history_days: int = 14
    min_skips: int = 3
    skip_ratio: float = 0.5
    replace: bool = True  # one-for-one replacements
    seed_artists: list[str] | None = None

    @classmethod
    def from_app(cls, app: AppContainer, **overrides: Any) -> RefreshSettings:
        s = app.settings
        values: dict[str, Any] = {
            "min_days_in_playlist": s.refresh_min_days_in_playlist,
            "window_days": s.refresh_window_days,
            "min_history_days": s.refresh_min_history_days,
            "min_skips": s.refresh_min_skips,
            "skip_ratio": s.refresh_skip_ratio,
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)


def _label(e: PlaylistEntry) -> str:
    return f"{e.artist} - {e.name}"


def _info(e: PlaylistEntry) -> TrackInfo:
    return TrackInfo(
        title=e.name,
        artist_name=e.artist,
        catalog_id=e.catalog_id,
        library_id=None if e.catalog_id else e.library_id,
        duration_ms=e.duration_ms,
    )


def item_song_id(item: dict[str, Any]) -> str:
    return str(item.get("catalog_id") or item.get("library_id"))


async def find_playlist(app: AppContainer, name: str) -> Playlist | None:
    for pl in await app.library.list_playlists(limit=500):
        if pl.name == name:
            return pl
    return None


class RefreshService:
    def __init__(self, app: AppContainer, music_app: MusicAppReader | None = None):
        self.app = app
        self.music_app = music_app or MusicAppReader()
        self.proposals = ProposalRepository(app.db)
        self.managed = ManagedPlaylistRepository(app.db)

    # ----------------------------------------------------------------- helpers
    async def _music_app_stats(
        self, playlist: Playlist
    ) -> tuple[dict[tuple[str, str], TrackStats], str | None]:
        try:
            stats = await self.music_app.playlist_stats(playlist.name)
        except MusicAppUnavailable as exc:
            return {}, str(exc)
        return {s.key: s for s in stats}, None

    async def _ratings(self, ids: list[str]) -> dict[str, int]:
        ids = [i for i in ids if i and not i.startswith("i.")]
        if not ids:
            return {}
        try:
            return await self.app.taste.ratings(ids)
        except AppleMusicError:
            return {}

    def _top_artists(self, entries: list[PlaylistEntry], n: int) -> list[str]:
        counts: dict[str, tuple[int, str]] = {}
        for e in entries:
            key = primary_artist(e.artist)
            count, name = counts.get(key, (0, e.artist))
            counts[key] = (count + 1, name)
        return [name for _, name in sorted(counts.values(), key=lambda c: -c[0])[:n]]

    async def _archive_ids(self, playlist: Playlist, managed: dict[str, Any] | None) -> set[str]:
        archive_id = (managed or {}).get("archive_apple_playlist_id")
        if not archive_id:
            found = await find_playlist(self.app, playlist.name + ARCHIVE_SUFFIX)
            archive_id = found.id if found else None
        if not archive_id:
            return set()
        try:
            return {TrackRef.of(e).id for e in await self.app.library.playlist_tracks(archive_id)}
        except AppleMusicError:
            return set()

    def _recently_added(self, managed: dict[str, Any] | None, since: datetime) -> set[str]:
        """Track ids added to a managed playlist after ``since`` (from its history)."""
        if not managed:
            return set()
        out = set()
        for ev in PlaylistEventRepository(self.app.db).history(managed["id"], limit=500):
            if (
                ev["event"] == "added"
                and datetime.fromisoformat(ev["occurred_at"]).replace(tzinfo=None) >= since
            ):
                out.add(str(ev["catalog_id"]))
        return out

    # -------------------------------------------------------------------- plan
    async def build_plan(
        self,
        playlist_id: str,
        settings: RefreshSettings | None = None,
        *,
        job_run_id: int | None = None,
        today: date | None = None,
    ) -> dict[str, Any]:
        """Store a refresh proposal for ``playlist_id`` and return it rendered. Never writes to Apple."""
        from .proposals import ProposalService

        settings = settings or RefreshSettings.from_app(self.app)
        managed = self.managed.get(playlist_id)
        existing = self.proposals.open_for(
            "refresh",
            managed_playlist_id=managed["id"] if managed else None,
            playlist_id=None if managed else playlist_id,
        )
        if existing:
            return {**ProposalService(self.app).render(existing["id"]), "already_open": True}

        today = today or date.today()
        playlist = await self.app.library.get_playlist(playlist_id)
        entries = await self.app.library.playlist_tracks(playlist_id)
        window = ListeningRepository(self.app.db).window(today - timedelta(days=settings.window_days), today)
        stats, stats_error = await self._music_app_stats(playlist)
        ratings = await self._ratings([e.catalog_id or "" for e in entries])
        cutoff = datetime.combine(today, datetime.min.time()) - timedelta(days=settings.min_days_in_playlist)
        recent_events = self._recently_added(managed, cutoff)
        notes: list[str] = []
        if stats_error:
            notes.append(f"Music app not readable ({stats_error}); dates added come from the history.")

        removals: list[tuple[PlaylistEntry, dict[str, Any]]] = []
        disliked_ids: set[str] = set()
        for e in entries:
            d = window.deltas.get(match_key(e.artist, e.name))
            s = stats.get(track_key(e.artist, e.name))
            if ratings.get(e.catalog_id or "") == -1 or (d and d.disliked) or (s and s.disliked):
                disliked_ids.add(TrackRef.of(e).id)
                removals.append((e, {"why": "disliked"}))

        rotation = self._rotation(
            entries, window, stats, ratings, recent_events, disliked_ids, cutoff.date(), settings, notes
        )
        removals.extend(rotation)

        replacements: list[tuple[int, dict[str, Any]]] = []
        if removals and settings.replace:
            try:
                replacements = await self._replacements(playlist, managed, entries, removals, settings, notes)
            except AppleMusicError as exc:  # removals still stand; the owner can ask for picks in chat
                notes.append(f"Replacements unavailable this time ({type(exc).__name__}).")

        items: list[ProposedTrack] = []
        evidence: dict[str, dict[str, Any]] = {}
        for e, ev in removals:
            items.append(ProposedTrack(_info(e), action="remove"))
            evidence[TrackRef.of(e).id] = ev
        for index, cand in replacements:
            items.append(
                ProposedTrack(
                    TrackInfo(
                        title=cand["name"],
                        artist_name=cand["artist"],
                        catalog_id=cand["id"],
                        album=cand.get("album"),
                        genre=(cand.get("genres") or [None])[0],
                        duration_ms=cand.get("duration_ms"),
                    ),
                    action="add",
                    bucket=cand["bucket"],
                    replaces=index,
                )
            )
            evidence[cand["id"]] = {"why": "replacement", "source": cand["source"]}
        if not removals:
            notes.append("Nothing to rotate this week.")

        purpose = (managed or {}).get("profile") or (managed or {}).get("purpose")
        proposal_id = self.proposals.create(
            "refresh",
            items,
            job_run_id=job_run_id,
            managed_playlist_id=managed["id"] if managed else None,
            params={
                "playlist_id": playlist_id,
                "playlist_name": playlist.name,
                "purpose": purpose,
                "tracks_in_playlist": len(entries),
                "window": {
                    "start": window.start.isoformat(),
                    "end": window.end.isoformat(),
                    "history_days": window.history_days,
                },
                "evidence": evidence,
                "notes": notes,
            },
        )
        return {**ProposalService(self.app).render(proposal_id), "already_open": False}

    def _rotation(
        self,
        entries: list[PlaylistEntry],
        window: ListeningWindow,
        stats: dict[tuple[str, str], TrackStats],
        ratings: dict[str, int],
        recent_events: set[str],
        disliked_ids: set[str],
        cutoff: date,
        settings: RefreshSettings,
        notes: list[str],
    ) -> list[tuple[PlaylistEntry, dict[str, Any]]]:
        if window.history_days < settings.min_history_days:
            notes.append(
                f"Only {window.history_days} day(s) of play history (the daily snapshot started "
                f"{window.history_start or 'not yet'}); rotation needs {settings.min_history_days}. "
                "No track is proposed for rotation yet, only dislikes."
            )
            return []
        observed: list[tuple[PlaylistEntry, Delta]] = []
        for e in entries:
            d = window.deltas.get(match_key(e.artist, e.name))
            if d is not None:
                observed.append((e, d))
        if not observed or median(d.plays for _, d in observed) < 1:
            notes.append(
                f"The playlist was not played in the last {settings.window_days} days: "
                "nothing to learn, no rotation proposed."
            )
            return []
        eligible = []
        for e, d in observed:
            ref = TrackRef.of(e).id
            s = stats.get(track_key(e.artist, e.name))
            loved = d.favorited or ratings.get(e.catalog_id or "") == 1 or bool(s and s.favorited)
            if s and s.date_added is not None:
                recent = s.date_added.date() > cutoff
            else:
                recent = d.first_seen > cutoff or ref in recent_events
            if ref in disliked_ids or loved or recent or ref in recent_events:
                continue
            eligible.append((e, d))
        skipped = sorted(
            (
                (e, d)
                for e, d in eligible
                if d.skips >= settings.min_skips and (d.skip_ratio or 0) >= settings.skip_ratio
            ),
            key=lambda ed: (-(ed[1].skip_ratio or 0), -ed[1].skips),
        )
        skipped_ids = {TrackRef.of(e).id for e, _ in skipped}
        unplayed = sorted(
            ((e, d) for e, d in eligible if d.plays == 0 and TrackRef.of(e).id not in skipped_ids),
            key=lambda ed: ed[1].first_seen,
        )
        limit = max(1, round(len(entries) * settings.rotate_fraction))
        out: list[tuple[PlaylistEntry, dict[str, Any]]] = []
        for e, d in skipped:
            out.append(
                (e, {"why": "often_skipped", "plays": d.plays, "skips": d.skips, "skip_ratio": d.skip_ratio})
            )
        for e, d in unplayed:
            out.append((e, {"why": "not_played", "plays": 0, "skips": d.skips}))
        if len(out) > limit:
            notes.append(f"{len(out) - limit} more candidate(s) left for later weeks (cap {limit}).")
        return out[:limit]

    async def _replacements(
        self,
        playlist: Playlist,
        managed: dict[str, Any] | None,
        entries: list[PlaylistEntry],
        removals: list[tuple[PlaylistEntry, dict[str, Any]]],
        settings: RefreshSettings,
        notes: list[str],
    ) -> list[tuple[int, dict[str, Any]]]:
        """One candidate per removal, same familiarity bucket when possible (keeps the mix)."""
        discovery = self.app.discovery
        seeds = settings.seed_artists or self._top_artists(entries, 5)
        in_main = {TrackRef.of(e).id for e in entries}
        excluded = in_main | await self._archive_ids(playlist, managed)
        excluded |= {str(t["catalog_id"]) for t in self.proposals.rejected_tracks(500)}
        genres = {g for g in (_genre(e) for e in await self._catalog_songs(entries)) if g}
        index = await discovery.index([e.catalog_id for e, _ in removals if e.catalog_id])
        pools: dict[str, list[dict[str, Any]]] = {}

        async def pool(bucket: str) -> list[dict[str, Any]]:
            if bucket not in pools:
                rows: list[dict[str, Any]] = []
                if bucket == KNOWN_TRACK:
                    rows = (await discovery.known_tracks(seeds, limit=30)).get("tracks", [])
                elif bucket == KNOWN_ARTIST_NEW_TRACK:
                    rows = (await discovery.deep_cuts(seeds, per_artist=3))["tracks"]
                else:
                    rows = (await discovery.discover(seeds, per_artist=2, limit=20)).candidates
                rows = [{**r, "bucket": bucket, "source": _SOURCES[bucket]} for r in rows]
                # Candidates in the playlist's own genres first (the profile's coherence proxy).
                rows.sort(key=lambda r: 0 if set(r.get("genres") or []) & genres else 1)
                pools[bucket] = rows
            return pools[bucket]

        banned = self.app.blacklist()
        chosen: list[tuple[int, dict[str, Any]]] = []
        missing = 0
        for index_pos, (e, _) in enumerate(removals):
            bucket = index.bucket(e.catalog_id or "", e.artist, e.name) if index else NEW_ARTIST
            if bucket == PARTLY_KNOWN:
                bucket = KNOWN_ARTIST_NEW_TRACK
            order = [bucket] + [b for b in (NEW_ARTIST, KNOWN_ARTIST_NEW_TRACK, KNOWN_TRACK) if b != bucket]
            pick = None
            for b in order:
                for cand in await pool(b):
                    cid = str(cand["id"])
                    if cid in excluded or cid.startswith("i."):
                        continue
                    if banned.blocked_reason(cid, cand["artist"], cand["name"]):
                        continue
                    if index and index.is_disliked(cid, cand["artist"], cand["name"]):
                        continue
                    pick = cand
                    break
                if pick:
                    break
            if pick is None:
                missing += 1
                continue
            excluded.add(str(pick["id"]))
            chosen.append((index_pos, pick))
        if missing:
            notes.append(f"No coherent replacement found for {missing} track(s): the playlist would shrink.")
        return chosen

    async def _catalog_songs(self, entries: list[PlaylistEntry]) -> list[Any]:
        ids = [e.catalog_id for e in entries if e.catalog_id]
        if not ids:
            return []
        try:
            return await self.app.catalog.get_songs(ids)
        except AppleMusicError:
            return []

    # ------------------------------------------------------------------- apply
    async def archive_and_remove(
        self,
        playlist_id: str,
        remove_ids: list[str],
        *,
        allow_rebuild: bool = False,
        origin: str = "refresh",
        proposal_id: int | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Copy the tracks to "<name> · Arquivo", verify the copy, then remove them (ADR 0010)."""
        from .sessions import sync_with_history

        playlist = await self.app.library.get_playlist(playlist_id)
        entries = await self.app.library.playlist_tracks(playlist_id)
        by_id = {TrackRef.of(e).id: e for e in entries}
        result: dict[str, Any] = {"playlist": playlist.to_dict(), "warnings": []}
        unknown = [i for i in remove_ids if i not in by_id]
        if unknown:
            result["warnings"].append(f"Not in the playlist, ignored: {unknown}")
        to_remove = [i for i in remove_ids if i in by_id]
        if not to_remove:
            return result

        managed = self.managed.get(playlist_id)
        archive = None
        if managed and managed.get("archive_apple_playlist_id"):
            try:
                archive = await self.app.library.get_playlist(managed["archive_apple_playlist_id"])
            except AppleMusicError:
                archive = None
        archive = archive or await find_playlist(self.app, playlist.name + ARCHIVE_SUFFIX)
        if archive is None:
            archive = await self.app.library.create_playlist(
                playlist.name + ARCHIVE_SUFFIX, f"Tracks that left '{playlist.name}'. Nothing is lost."
            )
        if managed and managed.get("archive_apple_playlist_id") != archive.id:
            self.managed.upsert(playlist_id, origin=managed["origin"], archive_apple_playlist_id=archive.id)
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
        result["archive_playlist"] = archive.to_dict()
        if managed:
            PlaylistEventRepository(self.app.db).record(
                managed["id"],
                [(_info(by_id[i]), "archived", None) for i in to_remove],
                origin=origin,
                proposal_id=proposal_id,
                reason=reason,
            )
        desired = [i for i in by_id if i not in set(to_remove)]
        edit = await sync_with_history(
            self.app,
            playlist_id,
            desired,
            allow_rebuild,
            origin=origin,
            reason=reason,
            proposal_id=proposal_id,
        )
        result["removed"] = {"tracks": [_label(by_id[i]) for i in to_remove], "edit": edit.to_dict()}
        result["playlist_id"] = edit.playlist.id
        if edit.status == "needs_decision":
            result["status"] = "needs_decision"
            result["warnings"].append(
                "Removal needs a decision (see removed.edit.decision); archive copy is safe."
            )
        return result

    async def apply_refresh(
        self,
        proposal_id: int,
        *,
        purpose: str | None = None,
        estimates: list[dict[str, Any]] | None = None,
        allow_rebuild: bool = False,
    ) -> dict[str, Any]:
        """Apply the accepted items of an approved refresh proposal (nothing else, ever)."""
        from .operations import insert_into_playlist

        p = self.proposals.get(proposal_id)
        if p is None or p["kind"] != "refresh":
            raise ValueError(f"proposal {proposal_id} is not a refresh proposal")
        if p["status"] != "approved":
            raise ValueError(f"proposal {proposal_id} is {p['status']}; only an approved proposal is applied")
        params = p["params"] or {}
        playlist_id = params["playlist_id"]
        if p["managed_playlist_id"]:
            managed = self.managed.get_by_id(p["managed_playlist_id"])
            playlist_id = managed["apple_playlist_id"] if managed else playlist_id
        accepted = [i for i in p["items"] if i["decision"] == "accepted"]
        removes = [item_song_id(i) for i in accepted if i["action"] == "remove"]
        adds = [i for i in accepted if i["action"] == "add"]
        result: dict[str, Any] = {"proposal_id": proposal_id, "status": "applied", "warnings": []}
        if removes:
            removed = await self.archive_and_remove(
                playlist_id,
                removes,
                allow_rebuild=allow_rebuild,
                proposal_id=proposal_id,
                reason="weekly refresh",
            )
            result.update({k: v for k, v in removed.items() if k != "warnings"})
            result["warnings"] += removed["warnings"]
            if removed.get("status") == "needs_decision":
                return result
            playlist_id = removed.get("playlist_id", playlist_id)
        if adds:
            given = {str(t["id"]): t for t in estimates or []}
            new_tracks = [
                {
                    **given.get(item_song_id(i), {}),
                    "id": item_song_id(i),
                    "artist": i["artist_name"],
                    "title": i["title"],
                }
                for i in adds
            ]
            inserted = await insert_into_playlist(
                self.app,
                playlist_id,
                purpose or params.get("purpose") or "",
                new_tracks,
                [t for t in estimates or [] if str(t["id"]) not in {n["id"] for n in new_tracks}],
                allow_rebuild,
                origin="refresh",
                proposal_id=proposal_id,
            )
            result["added"] = inserted
            if (inserted.get("edit") or {}).get("status") == "needs_decision":
                result["status"] = "needs_decision"
                result["warnings"].append("Removals are done; the additions need a decision (ask the user).")
                return result
        self.proposals.set_status(proposal_id, "applied")
        return result

    async def apply(
        self,
        playlist_id: str,
        purpose: str,
        remove_ids: list[str] | None = None,
        allow_rebuild: bool = False,
    ) -> dict[str, Any]:
        """Remove tracks the owner named directly (outside a proposal): archived first, then removed."""
        del purpose  # kept for the tool's signature; removals do not need the profile
        return await self.archive_and_remove(
            playlist_id,
            [str(i) for i in remove_ids or []],
            allow_rebuild=allow_rebuild,
            reason="owner request",
        )


_SOURCES = {KNOWN_TRACK: "known_tracks", KNOWN_ARTIST_NEW_TRACK: "deep_cuts", NEW_ARTIST: "discover_tracks"}


def _genre(song: Any) -> str | None:
    genres = getattr(song, "genres", None) or []
    return str(genres[0]) if genres else None
