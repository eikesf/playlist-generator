"""Refresh tools: a read-only refresh proposal on request, and removals the owner names directly."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..refresh import MAX_GROW_BY, RefreshService, RefreshSettings
from .base import ContainerProvider, tool_errors


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    @mcp.tool()
    @tool_errors
    async def plan_playlist_refresh(
        playlist_id: str,
        rotate_fraction: float = 0.2,
        seed_artists: list[str] | None = None,
        grow_by: int | None = None,
    ) -> dict[str, Any]:
        """Build a refresh proposal now (the weekly job does the same for opted-in playlists).
        Nothing changes in Apple Music: the proposal lists tracks to remove (often skipped, not played
        lately, disliked) with their plays/skips, and one replacement per removal coherent with the
        playlist. `grow_by` adds that many extra tracks on top (default: the playlist's own
        `refresh_grow_by`, 0 = keep the size). If one is already open, that one comes back
        (`already_open`). Then show it to the user and decide item by item with decide_proposal, and
        apply_proposal once approved."""
        if not 0 < rotate_fraction <= 0.5:
            raise ToolError("rotate_fraction must be between 0 and 0.5")
        if grow_by is not None and not 0 <= grow_by <= MAX_GROW_BY:
            raise ToolError(f"grow_by must be between 0 and {MAX_GROW_BY}")
        app = provider.get()
        settings = RefreshSettings.from_app(
            app, rotate_fraction=rotate_fraction, seed_artists=seed_artists, grow_by=grow_by
        )
        return await RefreshService(app, app.music_app).build_plan(playlist_id, settings)

    @mcp.tool()
    @tool_errors
    async def apply_playlist_refresh(
        playlist_id: str,
        purpose: str,
        remove_ids: list[str] | None = None,
        allow_rebuild: bool = False,
    ) -> dict[str, Any]:
        """Remove tracks the user named or confirmed directly, outside a proposal. Each one is first
        copied to "<name> · Arquivo" (nothing is ever lost) and the copy is verified, then removed
        keeping the rest in order. If an edit returns status 'needs_decision', ask the user (recreate
        vs manual) before calling again with allow_rebuild=true."""
        if not remove_ids:
            raise ToolError("Nothing to apply: pass remove_ids confirmed by the user.")
        app = provider.get()
        return await RefreshService(app, app.music_app).apply(
            playlist_id, purpose, remove_ids=remove_ids, allow_rebuild=allow_rebuild
        )
