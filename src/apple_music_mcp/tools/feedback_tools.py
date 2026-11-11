"""Feedback tools: "não gostei" (removal suggestion + reason) and the explicit blacklist."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from ..sessions import track_info
from .base import ContainerProvider, tool_errors


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    @mcp.tool()
    @tool_errors
    async def dislike_track(
        song_id: str, reason: str, playlist_id: str | None = None, session_id: int | None = None
    ) -> dict[str, Any]:
        """Record that the user does not like a track ("não gostei"). Ask WHY first and pass the reason
        in their words; it improves future picks. This is only a suggestion to remove it: nothing is
        removed here. If it is in a playlist, ask before calling remove_tracks_from_playlist, then call
        resolve_dislike. Never blacklist the artist because of one disliked track."""
        app = provider.get()
        songs = await app.catalog.get_songs([song_id])
        if not songs:
            raise ValueError(f"song {song_id} not found in the catalog")
        feedback_id = app.feedback.dislike(
            track_info(songs[0]), reason, playlist_id=playlist_id, session_id=session_id
        )
        return {
            "feedback_id": feedback_id,
            "track": songs[0].label,
            "suggestion": "remove from the playlist" if playlist_id else "avoid in future playlists",
            "note": "Ask the user to confirm before removing. The artist is NOT blocked.",
        }

    @mcp.tool()
    @tool_errors
    async def resolve_dislike(feedback_id: int, outcome: Literal["removed", "kept"]) -> dict[str, Any]:
        """Close a dislike after the user decided: the track was removed, or they chose to keep it."""
        return {"feedback_id": feedback_id, "updated": provider.get().feedback.resolve(feedback_id, outcome)}

    @mcp.tool()
    @tool_errors
    async def list_dislikes(open_only: bool = False, limit: int = 50) -> dict[str, Any]:
        """Tracks the user disliked, with reasons and whether they were removed. Use the reasons to
        steer recommendations (e.g. "vocal irritante" means avoid similar vocals, not the artist)."""
        rows = provider.get().feedback.list(open_only=open_only, limit=limit)
        return {"count": len(rows), "dislikes": rows}

    @mcp.tool()
    @tool_errors
    async def blacklist_add(
        scope: Literal["artist", "track"], value: str, owner_request: str
    ) -> dict[str, Any]:
        """Block an artist (value: the artist name) or a track (value: catalog song id) from every
        playlist. ONLY when the user explicitly asks ("nunca mais coloca X"): never infer it from a
        dislike. `owner_request`: the user's request, verbatim."""
        app = provider.get()
        if scope == "artist":
            entry = app.blacklist_entries.add_artist(value.strip(), owner_request)
            label = value.strip()
        else:
            songs = await app.catalog.get_songs([value])
            if not songs:
                raise ValueError(f"song {value} not found in the catalog")
            entry = app.blacklist_entries.add_track(track_info(songs[0]), owner_request)
            label = songs[0].label
        return {"blacklist_id": entry, "blocked": label, "scope": scope}

    @mcp.tool()
    @tool_errors
    async def blacklist_remove(blacklist_id: int) -> dict[str, Any]:
        """Lift a blacklist entry when the user asks (the entry is kept in the history, inactive)."""
        return {"blacklist_id": blacklist_id, "lifted": provider.get().blacklist_entries.lift(blacklist_id)}

    @mcp.tool()
    @tool_errors
    async def blacklist_list() -> dict[str, Any]:
        """Active blacklist entries (artists and tracks that never go into a playlist)."""
        items = provider.get().blacklist_entries.active()
        return {
            "count": len(items),
            "entries": [
                {"blacklist_id": i.id, "scope": i.scope, "blocked": i.label, "owner_request": i.owner_request}
                for i in items
            ],
        }
