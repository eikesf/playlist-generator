"""Weekly Discoveries tools (guide §7.11), used by the owner's Claude Desktop scheduled task."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ..weekly_discoveries import WeeklyDiscoveries
from .base import ContainerProvider, tool_errors


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    @mcp.tool()
    @tool_errors
    async def weekly_discoveries_context(playlist_id: str | None = None) -> dict[str, Any]:
        """Read-only. Without `playlist_id`: the managed playlists with Weekly Discoveries on. With it:
        everything needed to curate this week's ~30 new tracks for that playlist: profile, mix, top
        artists, genres, recent history, dislikes, blacklist, tracks to avoid, last week's batch, and
        candidate pools from the catalog (`new_artists`, `deep_cuts`). Choose by vibe; the pools are
        a starting point, not the answer."""
        return await WeeklyDiscoveries(provider.get()).context(playlist_id)

    @mcp.tool()
    @tool_errors
    async def submit_weekly_discoveries(
        playlist_id: str, song_ids: list[str], notes: str | None = None
    ) -> dict[str, Any]:
        """WRITES to Apple Music: replaces last week's tracks in "<name> · Weekly Discoveries" with this
        batch (catalog ids). Only for the weekly scheduled task the user set up, or when he asks for
        it. Every id is checked (catalog, blacklist, dislikes, already heard, in the main playlist or
        its Arquivo, rejected before); invalid ones come back in `dropped` with the reason. A batch
        outside the size range is refused and nothing is written. The main playlist is never touched;
        liked tracks waiting for promotion stay. `notes`: one line on this week's theme."""
        return await WeeklyDiscoveries(provider.get()).submit(playlist_id, song_ids, notes)
