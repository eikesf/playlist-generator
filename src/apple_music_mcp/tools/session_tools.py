"""Session tools: edit or create, questions, preview before creating, managed playlists, history."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ..sessions import CurationSessions, PreviewTrack
from .base import ContainerProvider, tool_errors


class PreviewTrackInput(BaseModel):
    song_id: str = Field(description="Catalog id from resolve_tracks, in playlist order")
    section: str | None = Field(default=None, description="Part of the set (act name from order_tracks)")
    familiarity: str | None = Field(default=None, description="Bucket from resolve_tracks")


class Rejection(BaseModel):
    song_id: str
    reason: str | None = Field(default=None, description="Why the user does not want it, in their words")


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    def sessions() -> CurationSessions:
        return CurationSessions(provider.get())

    @mcp.tool()
    @tool_errors
    async def start_session(
        intent: Literal["create", "edit"],
        request: str,
        playlist_id: str | None = None,
        channel: Literal["desktop", "dispatch"] = "desktop",
    ) -> dict[str, Any]:
        """Start every playlist conversation here, after asking the user first: edit an existing
        playlist or create a new one? Pass the user's request in their words. For `edit`, pass the
        `playlist_id`: a managed playlist comes back with its saved purpose, profile and mix. Also
        returns `avoid`: tracks the user rejected before (with the reason) so you do not suggest them
        again. Keep the `session_id` for record_answer and preview_playlist."""
        return sessions().start(intent, request, channel, playlist_id)

    @mcp.tool()
    @tool_errors
    async def record_answer(session_id: int, question: str, answer: str) -> dict[str, Any]:
        """Save a question you asked and the user's answer (purpose, mood, artists, mix, duration,
        restrictions). There is no limit on questions: keep asking until the request is clear."""
        return {"session_id": session_id, "turn": sessions().record_answer(session_id, question, answer)}

    @mcp.tool()
    @tool_errors
    async def preview_playlist(
        name: str,
        tracks: list[PreviewTrackInput],
        session_id: int | None = None,
        description: str = "",
        purpose: str | None = None,
        duration_minutes: float | None = None,
        known_artists_pct: float | None = None,
        known_tracks_within_known_artists_pct: float | None = None,
        replaces_proposal_id: int | None = None,
    ) -> dict[str, Any]:
        """Save the proposed playlist and get it organized by the parts of the set, WITHOUT creating
        anything in Apple Music (the default flow: preview first, create only after the user agrees).
        Name and description in English. Show the result to the user section by section. To change
        it, call reject_preview_tracks and/or build a new preview with `replaces_proposal_id`."""
        from ..curation.familiarity import MixTargets

        params: dict[str, Any] = {"duration_minutes": duration_minutes}
        if known_artists_pct is not None:
            within = known_tracks_within_known_artists_pct
            targets = MixTargets.from_levels(known_artists_pct, within if within is not None else 30)
            params.update(
                known_artists_pct=targets.known_artists_pct, known_tracks_pct=targets.known_tracks_pct
            )
        return await sessions().preview(
            session_id,
            name,
            [PreviewTrack(t.song_id, t.section, t.familiarity) for t in tracks],
            description=description or None,
            purpose=purpose,
            params=params,
            replaces_proposal_id=replaces_proposal_id,
        )

    @mcp.tool()
    @tool_errors
    async def reject_preview_tracks(proposal_id: int, rejections: list[Rejection]) -> dict[str, Any]:
        """Mark tracks of a preview as not wanted, with the user's reason ("não gosto dessa", "muito
        pesada"). The reasons are kept so future sessions avoid them. Rejecting a track never bans
        the artist."""
        return sessions().reject(proposal_id, [(r.song_id, r.reason) for r in rejections])

    @mcp.tool()
    @tool_errors
    async def approve_preview(proposal_id: int) -> dict[str, Any]:
        """Create the previewed playlist in Apple Music (rejected tracks left out, order kept) once the
        user approved it. It becomes a managed playlist: its purpose, profile and mix are remembered."""
        return await sessions().approve(proposal_id)

    @mcp.tool()
    @tool_errors
    async def adopt_playlist(
        playlist_id: str,
        name: str,
        purpose: str,
        known_artists_pct: float | None = None,
        known_tracks_within_known_artists_pct: float | None = None,
        duration_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Make an existing Apple Music playlist a managed one, remembering its purpose, profile and
        mix, so future edits and refreshes respect them."""
        from ..curation.familiarity import MixTargets

        fields: dict[str, Any] = {"target_duration_min": duration_minutes}
        if known_artists_pct is not None:
            within = known_tracks_within_known_artists_pct
            t = MixTargets.from_levels(known_artists_pct, within if within is not None else 30)
            fields.update(known_artists_pct=t.known_artists_pct, known_tracks_pct=t.known_tracks_pct)
        return sessions().adopt(playlist_id, name, purpose, **fields)

    @mcp.tool()
    @tool_errors
    async def list_managed_playlists() -> dict[str, Any]:
        """Playlists the system manages, with purpose, profile, mix, size targets and refresh settings."""
        rows = sessions().managed.list()
        return {"count": len(rows), "playlists": rows}

    @mcp.tool()
    @tool_errors
    async def update_managed_playlist(
        playlist_id: str,
        purpose: str | None = None,
        known_artists_pct: float | None = None,
        known_tracks_within_known_artists_pct: float | None = None,
        duration_minutes: int | None = None,
        refresh_enabled: bool | None = None,
        discoveries_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Change what is remembered about a managed playlist. Changing `purpose` redefines its whole
        profile (guide §7.1): confirm with the user first. `refresh_enabled` / `discoveries_enabled`
        opt the playlist in or out of the weekly refresh and "Descobertas da semana"."""
        from ..curation.familiarity import MixTargets

        s = sessions()
        current = s.managed.get(playlist_id)
        if current is None:
            raise ValueError("not a managed playlist: use adopt_playlist first")
        fields: dict[str, Any] = {}
        if duration_minutes is not None:
            fields["target_duration_min"] = duration_minutes
        if refresh_enabled is not None:
            fields["refresh_enabled"] = refresh_enabled
        if discoveries_enabled is not None:
            fields["discoveries_enabled"] = discoveries_enabled
        if known_artists_pct is not None:
            within = known_tracks_within_known_artists_pct
            t = MixTargets.from_levels(known_artists_pct, within if within is not None else 30)
            fields.update(known_artists_pct=t.known_artists_pct, known_tracks_pct=t.known_tracks_pct)
        profile_slug = None
        if purpose is not None:
            fields["purpose"] = purpose
            profile_slug = provider.get().profiles.get(purpose).key
        s.managed.upsert(playlist_id, origin=current["origin"], profile_slug=profile_slug, **fields)
        return {"managed_playlist": s.managed.get(playlist_id)}

    @mcp.tool()
    @tool_errors
    async def playlist_history(playlist_id: str, limit: int = 50) -> dict[str, Any]:
        """What happened to a managed playlist: tracks added, removed, moved or archived, when, by which
        flow (session, refresh, promotion) and why."""
        s = sessions()
        managed = s.managed.get(playlist_id)
        if managed is None:
            return {"managed": False, "events": []}
        return {"managed": True, "events": s.events.history(managed["id"], limit)}
