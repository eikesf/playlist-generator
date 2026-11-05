"""Library tools: create playlists, add tracks, list and inspect playlists."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..operations import PendingPlaylists, create_with_tracks
from ..resolver import resolve_tracks
from .base import ContainerProvider, tool_errors

SYNC_NOTE = "Library playlists sync to iPhone/Mac via iCloud Music Library within a few minutes."


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    @mcp.tool()
    @tool_errors
    async def create_playlist(
        name: str,
        description: str = "",
        song_ids: list[str] | None = None,
        tracks: list[str] | None = None,
        include_low_confidence: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create a playlist in the user's Apple Music library, keeping the given order.

        Pass `song_ids` (catalog ids from `resolve_tracks`) or `tracks` (free text lines, resolved
        here). With `dry_run=true` nothing is created; you get what would be added.
        If the Apple Music login expired, the playlist is saved as pending (nothing is lost): ask the
        user to run `apple-music-mcp login`, then call `finish_pending_playlists`.
        """
        if not name.strip():
            raise ToolError("name is required")
        app = provider.get()
        ids = [str(i) for i in (song_ids or [])]
        report_dict: dict[str, Any] | None = None
        accepted = {"matched", "low_confidence"} if include_low_confidence else {"matched"}
        if tracks:
            report = await resolve_tracks(app.catalog, list(tracks))
            report_dict = report.to_dict()
            ids += [r.song.id for r in report.resolutions if r.status in accepted and r.song]
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise ToolError("No tracks to add: nothing matched in the catalog.")
        if dry_run:
            return {
                "dry_run": True,
                "name": name,
                "would_add": len(ids),
                "song_ids": ids,
                "resolution": report_dict,
            }
        result: dict[str, Any] = await create_with_tracks(app, name.strip(), description.strip() or None, ids)
        result["note"] = SYNC_NOTE
        if report_dict:
            result["resolution_summary"] = report_dict["summary"]
            result["not_added"] = [t for t in report_dict["tracks"] if t["status"] not in accepted]
        return result

    @mcp.tool()
    @tool_errors
    async def add_tracks_to_playlist(playlist_id: str, song_ids: list[str]) -> dict[str, Any]:
        """Append catalog songs (ids from `resolve_tracks`) to an existing library playlist (id `p.xxxx`)."""
        added = await provider.get().library.add_tracks(playlist_id, [str(i) for i in song_ids])
        return {"playlist_id": playlist_id, "tracks_added": added}

    @mcp.tool()
    @tool_errors
    async def list_playlists(limit: int = 50) -> dict[str, Any]:
        """List playlists in the user's Apple Music library (id, name, url)."""
        playlists = await provider.get().library.list_playlists(limit=limit)
        return {"count": len(playlists), "playlists": [p.to_dict() for p in playlists]}

    @mcp.tool()
    @tool_errors
    async def get_playlist_tracks(playlist_id: str) -> dict[str, Any]:
        """List the tracks of a library playlist in order (to verify a creation or plan a follow-up)."""
        entries = await provider.get().library.playlist_tracks(playlist_id)
        return {"playlist_id": playlist_id, "count": len(entries), "tracks": [e.to_dict() for e in entries]}

    @mcp.tool()
    @tool_errors
    async def finish_pending_playlists(discard: list[str] | None = None) -> dict[str, Any]:
        """Create the playlists saved as pending because the login had expired (exact same tracks and
        order). Call after the user ran `apple-music-mcp login`. `discard`: names to drop instead."""
        pending = PendingPlaylists(provider.get())
        dropped = [n for n in discard or [] if pending.discard(n)]
        if not pending.list():
            return {"created": [], "discarded": dropped, "note": "No pending playlists."}
        created = await pending.finish()
        return {"created": created, "discarded": dropped, "note": SYNC_NOTE}
