"""Curation sessions: edit-or-create, questions, preview, approval, managed playlists (guide §7.1-7.7, §8.1).

The flow Claude follows:
1. ``start`` (edit or create). Editing a managed playlist loads its saved purpose, profile and mix.
2. ``record_answer`` for each question/answer that shapes the playlist (no limit on questions).
3. ``preview``: the proposed tracks, organized by the parts of the set. Nothing is created.
4. ``reject`` tracks the owner does not want (with the reason), then a new ``preview`` that replaces
   the previous one. Rejections are kept so later sessions do not suggest the same tracks again.
5. ``approve``: creates the playlist in Apple Music, registers it as managed, records the history.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .container import AppContainer
from .db.history import (
    ManagedPlaylistRepository,
    PlaylistEventRepository,
    ProposalRepository,
    ProposedTrack,
    SessionRepository,
)
from .db.repositories import TrackInfo
from .editing import EditResult
from .models import Song, format_duration


@dataclass
class PreviewTrack:
    song_id: str
    section: str | None = None
    bucket: str | None = None


def track_info(song: Song) -> TrackInfo:
    return TrackInfo(
        title=song.name,
        artist_name=song.artist,
        catalog_id=song.id,
        album=song.album,
        genre=song.genres[0] if song.genres else None,
        duration_ms=song.duration_ms,
        isrc=song.isrc,
        explicit=song.explicit,
    )


class CurationSessions:
    def __init__(self, app: AppContainer):
        self.app = app
        self.sessions = SessionRepository(app.db)
        self.managed = ManagedPlaylistRepository(app.db)
        self.proposals = ProposalRepository(app.db)
        self.events = PlaylistEventRepository(app.db)

    # -- 1-2. session and questions ------------------------------------------------------------
    def start(
        self, intent: str, request: str | None, channel: str = "desktop", playlist_id: str | None = None
    ) -> dict[str, Any]:
        if intent not in ("create", "edit"):
            raise ValueError("intent must be 'create' or 'edit'")
        session_id = self.sessions.start(channel, intent, request)
        out: dict[str, Any] = {"session_id": session_id, "intent": intent}
        if intent == "edit" and playlist_id:
            managed = self.managed.get(playlist_id)
            out["managed_playlist"] = managed
            out["note"] = (
                "Managed playlist: reuse its purpose, profile and mix unless the user changes them. "
                "Changing the purpose redefines the whole profile."
                if managed
                else "Not managed yet: ask its purpose and mix, then adopt_playlist to remember them."
            )
        out["avoid"] = self.proposals.rejected_tracks(50)
        return out

    def record_answer(self, session_id: int, question: str, answer: str | None) -> int:
        return self.sessions.add_turn(session_id, question, answer)

    # -- 3-4. preview, rejections ---------------------------------------------------------------
    async def preview(
        self,
        session_id: int | None,
        name: str,
        tracks: Sequence[PreviewTrack],
        *,
        description: str | None = None,
        purpose: str | None = None,
        params: dict[str, Any] | None = None,
        replaces_proposal_id: int | None = None,
        playlist_id: str | None = None,
    ) -> dict[str, Any]:
        ids = list(dict.fromkeys(t.song_id for t in tracks))
        if not ids:
            raise ValueError("a preview needs at least one track")
        songs = {s.id: s for s in await self.app.catalog.get_songs(ids)}
        missing = [i for i in ids if i not in songs]
        profile = self.app.profiles.get(purpose) if purpose else None
        managed = self.managed.get(playlist_id) if playlist_id else None
        by_id = {t.song_id: t for t in tracks}
        items = [
            ProposedTrack(track_info(songs[i]), section=by_id[i].section, bucket=by_id[i].bucket)
            for i in ids
            if i in songs
        ]
        proposal_id = self.proposals.create(
            "edit" if playlist_id else "create",
            items,
            session_id=session_id,
            managed_playlist_id=managed["id"] if managed else None,
            params={
                **(params or {}),
                "name": name,
                "description": description,
                "purpose": purpose,
                "profile": profile.key if profile else None,
                "playlist_id": playlist_id,
            },
        )
        if replaces_proposal_id is not None:
            self.proposals.set_status(replaces_proposal_id, "rejected")
        return {**self._render(proposal_id), "not_in_catalog": missing}

    def _render(self, proposal_id: int) -> dict[str, Any]:
        p = self.proposals.get(proposal_id)
        if p is None:
            raise ValueError(f"unknown proposal {proposal_id}")
        sections: list[dict[str, Any]] = []
        total_ms = 0
        for item in p["items"]:
            if item["decision"] == "rejected":
                continue
            name = item["section"] or "Playlist"
            if not sections or sections[-1]["section"] != name:
                sections.append({"section": name, "tracks": []})
            sections[-1]["tracks"].append(
                {
                    "song_id": item["catalog_id"],
                    "line": f"{item['artist_name']} - {item['title']}",
                    "duration": format_duration(item["duration_ms"]),
                    "familiarity": item["bucket"],
                }
            )
            total_ms += item["duration_ms"] or 0
        count = sum(len(s["tracks"]) for s in sections)
        return {
            "proposal_id": proposal_id,
            "status": p["status"],
            "name": p["params"].get("name"),
            "purpose": p["params"].get("profile") or p["params"].get("purpose"),
            "track_count": count,
            "total_duration": format_duration(total_ms),
            "sections": sections,
            "rejected": [
                {"line": f"{i['artist_name']} - {i['title']}", "reason": i["reason"]}
                for i in p["items"]
                if i["decision"] == "rejected"
            ],
            "next": "Show this preview by section. Apply the user's changes with reject_preview_tracks and a "
            "new preview_playlist (replaces_proposal_id), or create it with approve_preview.",
        }

    def reject(self, proposal_id: int, rejections: Sequence[tuple[str, str | None]]) -> dict[str, Any]:
        changed = self.proposals.decide_items(
            proposal_id, {sid: ("rejected", reason) for sid, reason in rejections}
        )
        return {"rejected": changed, **self._render(proposal_id)}

    # -- 5. approval ----------------------------------------------------------------------------
    async def approve(self, proposal_id: int) -> dict[str, Any]:
        from .operations import create_with_tracks

        p = self.proposals.get(proposal_id)
        if p is None:
            raise ValueError(f"unknown proposal {proposal_id}")
        if p["status"] not in ("draft", "pending_approval"):
            raise ValueError(
                f"proposal {proposal_id} is {p['status']}; only a pending preview can be approved"
            )
        if p["params"].get("playlist_id"):
            raise ValueError("this preview edits an existing playlist: apply it with insert/sync tools")
        keep = [i for i in p["items"] if i["decision"] != "rejected" and i["action"] == "add"]
        ids = [i["catalog_id"] for i in keep]
        params = p["params"]
        result = await create_with_tracks(self.app, params["name"], params.get("description"), ids)
        apple_id = result["playlist"]["id"]
        managed_id = self.managed.upsert(
            apple_id,
            origin="created",
            profile_slug=params.get("profile"),
            name=params["name"],
            purpose=params.get("purpose"),
            known_artists_pct=params.get("known_artists_pct"),
            known_tracks_pct=params.get("known_tracks_pct"),
            target_duration_min=params.get("duration_minutes"),
            target_track_count=params.get("track_count"),
        )
        self.proposals.decide_items(proposal_id, {i: ("accepted", None) for i in ids})
        self.proposals.set_status(proposal_id, "applied", managed_playlist_id=managed_id)
        self.events.record(
            managed_id,
            [
                (
                    TrackInfo(title=i["title"], artist_name=i["artist_name"], catalog_id=i["catalog_id"]),
                    "added",
                    n,
                )
                for n, i in enumerate(keep)
            ],
            origin="session",
            proposal_id=proposal_id,
        )
        if p["session_id"]:
            self.sessions.finish(p["session_id"])
        return {**result, "managed": True, "proposal_id": proposal_id}

    # -- managed playlists ----------------------------------------------------------------------
    def adopt(self, playlist_id: str, name: str, purpose: str | None, **fields: Any) -> dict[str, Any]:
        profile = self.app.profiles.get(purpose) if purpose else None
        managed_id = self.managed.upsert(
            playlist_id,
            origin="adopted",
            profile_slug=profile.key if profile else None,
            name=name,
            purpose=purpose,
            **fields,
        )
        return {"managed_playlist": self.managed.get_by_id(managed_id)}

    async def record_edit(
        self,
        playlist_id: str,
        added: Sequence[str],
        removed: Sequence[str],
        *,
        origin: str = "session",
        reason: str | None = None,
    ) -> int:
        """History for edits made through the tools on a managed playlist (no-op for others)."""
        managed = self.managed.get(playlist_id)
        if managed is None or not (added or removed):
            return 0
        songs = {s.id: s for s in await self.app.catalog.get_songs(list(dict.fromkeys([*added, *removed])))}
        events: list[tuple[TrackInfo, str, int | None]] = []
        for sid in added:
            if sid in songs:
                events.append((track_info(songs[sid]), "added", None))
        for sid in removed:
            if sid in songs:
                events.append((track_info(songs[sid]), "removed", None))
        return self.events.record(managed["id"], events, origin=origin, reason=reason)


async def sync_with_history(
    app: AppContainer,
    playlist_id: str,
    desired_ids: list[str],
    allow_rebuild: bool = False,
    *,
    origin: str = "session",
    reason: str | None = None,
) -> EditResult:
    """``PlaylistEditor.sync`` plus the managed playlist's history (added/removed tracks, new id after
    a rebuild). Playlists that are not managed are synced exactly as before."""
    sessions = CurationSessions(app)
    managed = sessions.managed.get(playlist_id)
    before = await app.editor.current_ids(playlist_id) if managed else []
    result = await app.editor.sync(playlist_id, desired_ids, allow_rebuild)
    if managed and result.status == "applied":
        if result.playlist.id != playlist_id:
            sessions.managed.move(playlist_id, result.playlist.id)
        added = [i for i in desired_ids if i not in set(before)]
        removed = [i for i in before if i not in set(desired_ids)]
        await sessions.record_edit(result.playlist.id, added, removed, origin=origin, reason=reason)
    return result
