"""Editing tools: reorder, remove, re-sequence and rename existing library playlists."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..curation.sequencer import TrackFeatures, order_tracks
from ..operations import enrich_features, insert_into_playlist
from ..sessions import sync_with_history
from .base import ContainerProvider, tool_errors
from .curation_tools import TrackInput


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    @mcp.tool()
    @tool_errors
    async def reorder_playlist(
        playlist_id: str, song_ids_in_order: list[str], allow_rebuild: bool = False
    ) -> dict[str, Any]:
        """Reorder an existing library playlist to exactly `song_ids_in_order` (ids as returned by
        `get_playlist_tracks` or `order_tracks`). The list must contain the same tracks as the playlist.
        Changes show up in the Music app on Mac/iPhone.
        If the result has status 'needs_decision', Apple refused the in-place edit: show the user
        `decision` (changes, manual_steps, recommendation) and ask: recreate the playlist (call again with
        allow_rebuild=true; old one deleted, link changes) or adjust by hand. Never pass allow_rebuild=true
        without that confirmation."""
        editor = provider.get().editor
        current = await editor.current_ids(playlist_id)
        if sorted(set(current)) != sorted(set(song_ids_in_order)):
            missing = sorted(set(current) - set(song_ids_in_order))
            extra = sorted(set(song_ids_in_order) - set(current))
            raise ToolError(
                f"reorder_playlist needs the same tracks. Missing: {missing}. Not in playlist: {extra}. "
                "Use sync_playlist or resequence_playlist to add/remove tracks."
            )
        return (await editor.sync(playlist_id, song_ids_in_order, allow_rebuild)).to_dict()

    @mcp.tool()
    @tool_errors
    async def sync_playlist(
        playlist_id: str, song_ids_in_order: list[str], allow_rebuild: bool = False
    ) -> dict[str, Any]:
        """Make a library playlist contain exactly `song_ids_in_order`, in that order: adds new tracks,
        removes tracks not in the list and reorders the rest, in one call.
        If the result has status 'needs_decision', Apple refused the in-place edit: show the user
        `decision` (changes, manual_steps, recommendation) and ask: recreate the playlist (call again with
        allow_rebuild=true; old one deleted, link changes) or adjust by hand. Never pass allow_rebuild=true
        without that confirmation."""
        return (
            await sync_with_history(provider.get(), playlist_id, song_ids_in_order, allow_rebuild)
        ).to_dict()

    @mcp.tool()
    @tool_errors
    async def remove_tracks_from_playlist(
        playlist_id: str, song_ids: list[str], allow_rebuild: bool = False
    ) -> dict[str, Any]:
        """Remove tracks (ids from `get_playlist_tracks`) from a playlist, keeping the rest in order.
        If the result has status 'needs_decision', Apple refused the in-place edit: show the user
        `decision` (changes, manual_steps, recommendation) and ask: recreate the playlist (call again with
        allow_rebuild=true; old one deleted, link changes) or adjust by hand. Never pass allow_rebuild=true
        without that confirmation."""
        app = provider.get()
        drop = {str(i) for i in song_ids}
        desired = [i for i in await app.editor.current_ids(playlist_id) if i not in drop]
        return (await sync_with_history(app, playlist_id, desired, allow_rebuild)).to_dict()

    @mcp.tool()
    @tool_errors
    async def resequence_playlist(
        playlist_id: str, purpose: str, tracks: list[TrackInput], allow_rebuild: bool = False
    ) -> dict[str, Any]:
        """Fully re-sequence an existing playlist (reshuffles everything): pass the full final track set
        with estimated bpm/energy; it is ordered from scratch along the purpose's energy curve and the
        playlist is synced. Only use when the user asks to reorganize the whole playlist. To ADD tracks
        while keeping the current order, use `insert_tracks_into_playlist` instead.
        If the result has status 'needs_decision', Apple refused the in-place edit: show the user
        `decision` (changes, manual_steps, recommendation) and ask: recreate the playlist (call again with
        allow_rebuild=true; old one deleted, link changes) or adjust by hand. Never pass allow_rebuild=true
        without that confirmation."""
        app = provider.get()
        features = [TrackFeatures.from_dict(t.model_dump()) for t in tracks]
        await enrich_features(app, features)
        sequence = order_tracks(provider.get().profiles.get(purpose), features)
        result = await sync_with_history(app, playlist_id, sequence["song_ids_in_order"], allow_rebuild)
        return {"edit": result.to_dict(), "sequence": sequence}

    @mcp.tool()
    @tool_errors
    async def insert_tracks_into_playlist(
        playlist_id: str,
        purpose: str,
        new_tracks: list[TrackInput],
        current_estimates: list[TrackInput] | None = None,
        allow_rebuild: bool = False,
    ) -> dict[str, Any]:
        """Add tracks to an existing playlist, each one at the exact position where it fits the vibe,
        WITHOUT changing the order of the tracks already there.

        `new_tracks`: verified catalog ids with estimated bpm/energy. `current_estimates`: bpm/energy
        estimates for the tracks already in the playlist (ids from `get_playlist_tracks`); the better
        these are, the better the placement. Returns where each track went (position, act, neighbours).
        If the result has status 'needs_decision', Apple refused the in-place edit: show the user
        `decision` (changes, manual_steps, recommendation) and ask: recreate the playlist (call again with
        allow_rebuild=true; old one deleted, link changes) or adjust by hand. Never pass allow_rebuild=true
        without that confirmation."""
        return await insert_into_playlist(
            provider.get(),
            playlist_id,
            purpose,
            [t.model_dump() for t in new_tracks],
            [t.model_dump() for t in current_estimates or []],
            allow_rebuild,
        )

    @mcp.tool()
    @tool_errors
    async def rename_playlist(
        playlist_id: str, name: str | None = None, description: str | None = None
    ) -> dict[str, Any]:
        """Rename a library playlist and/or change its description."""
        if not (name or description):
            raise ToolError("Pass a new name and/or description.")
        library = provider.get().library
        await library.update_playlist(playlist_id, name=name, description=description)
        return {"playlist": (await library.get_playlist(playlist_id)).to_dict()}
