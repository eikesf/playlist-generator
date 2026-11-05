"""Editing tools: reorder, remove, re-sequence and rename existing library playlists."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..curation.profiles import get_profile
from ..curation.sequencer import TrackFeatures, order_tracks
from .base import ContainerProvider, tool_errors
from .curation_tools import TrackInput


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    @mcp.tool()
    @tool_errors
    async def reorder_playlist(
        playlist_id: str, song_ids_in_order: list[str], allow_rebuild: bool = True
    ) -> dict[str, Any]:
        """Reorder an existing library playlist to exactly `song_ids_in_order` (ids as returned by
        `get_playlist_tracks` or `order_tracks`). The list must contain the same tracks as the playlist.
        Changes show up in the Music app on Mac/iPhone. If the account refuses in-place edits and
        `allow_rebuild` is true, the playlist is recreated with the same name (its link changes)."""
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
        playlist_id: str, song_ids_in_order: list[str], allow_rebuild: bool = True
    ) -> dict[str, Any]:
        """Make a library playlist contain exactly `song_ids_in_order`, in that order: adds new tracks,
        removes tracks not in the list and reorders the rest, in one call."""
        return (await provider.get().editor.sync(playlist_id, song_ids_in_order, allow_rebuild)).to_dict()

    @mcp.tool()
    @tool_errors
    async def remove_tracks_from_playlist(
        playlist_id: str, song_ids: list[str], allow_rebuild: bool = True
    ) -> dict[str, Any]:
        """Remove tracks (ids from `get_playlist_tracks`) from a playlist, keeping the rest in order."""
        editor = provider.get().editor
        drop = {str(i) for i in song_ids}
        desired = [i for i in await editor.current_ids(playlist_id) if i not in drop]
        return (await editor.sync(playlist_id, desired, allow_rebuild)).to_dict()

    @mcp.tool()
    @tool_errors
    async def resequence_playlist(
        playlist_id: str, purpose: str, tracks: list[TrackInput], allow_rebuild: bool = True
    ) -> dict[str, Any]:
        """Re-sequence an existing playlist after adjustments: pass the full final track set (existing
        tracks you keep + new ones, each with estimated bpm/energy). It orders them along the purpose's
        energy curve (same engine as `order_tracks`) and syncs the playlist: new tracks added, dropped
        tracks removed, everything in the right order. Use after adding songs to keep the set flowing."""
        app = provider.get()
        features = [TrackFeatures.from_dict(t.model_dump()) for t in tracks]
        catalog_ids = [f.id for f in features if not f.duration_ms and not f.id.startswith("i.")]
        if catalog_ids:
            try:
                durations = {s.id: s.duration_ms for s in await app.catalog.get_songs(catalog_ids)}
            except Exception:  # noqa: BLE001
                durations = {}
            for f in features:
                f.duration_ms = f.duration_ms or durations.get(f.id)
        sequence = order_tracks(get_profile(purpose), features)
        result = await app.editor.sync(playlist_id, sequence["song_ids_in_order"], allow_rebuild)
        return {"edit": result.to_dict(), "sequence": sequence}

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
