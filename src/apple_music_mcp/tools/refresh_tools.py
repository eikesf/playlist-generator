"""Refresh tools: weekly discovery staging, archive-before-remove, promote what the user likes."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..refresh import RefreshService, RefreshSettings
from .base import ContainerProvider, tool_errors
from .curation_tools import TrackInput


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    @mcp.tool()
    @tool_errors
    async def plan_playlist_refresh(
        playlist_id: str,
        rotate_fraction: float = 0.2,
        new_tracks: int | None = None,
        seed_artists: list[str] | None = None,
    ) -> dict[str, Any]:
        """Prepare a refresh WITHOUT touching the main playlist.

        - `rotation_candidates`: least-played tracks (from the Mac Music app's play counts), only when
          play data really tells them apart; recent additions and favorites are never candidates.
        - `removal_candidates`: tracks the user disliked.
        - New discoveries (similar artists, unknown to the user) are added to the staging playlist
          "<name> · Descobertas"; `promotion_candidates` are staging tracks they loved or played a lot.
        Then ASK the user which candidates to remove and which to promote, listing them by name.
        Never remove anything they did not confirm."""
        if not 0 < rotate_fraction <= 0.5:
            raise ToolError("rotate_fraction must be between 0 and 0.5")
        app = provider.get()
        settings = RefreshSettings(
            rotate_fraction=rotate_fraction, new_tracks=new_tracks, seed_artists=seed_artists
        )
        return await RefreshService(app, app.music_app).plan(playlist_id, settings)

    @mcp.tool()
    @tool_errors
    async def apply_playlist_refresh(
        playlist_id: str,
        purpose: str,
        remove_ids: list[str] | None = None,
        promote: list[TrackInput] | None = None,
        current_estimates: list[TrackInput] | None = None,
        allow_rebuild: bool = False,
    ) -> dict[str, Any]:
        """Apply ONLY what the user explicitly confirmed after `plan_playlist_refresh`.

        `remove_ids`: tracks the user agreed to drop; each is first copied to "<name> · Arquivo"
        (nothing is ever lost), then removed keeping the rest in order. `promote`: staging tracks to
        move into the main playlist (with estimated bpm/energy); each is inserted at the position that
        fits the vibe and removed from staging. If an edit returns status 'needs_decision', ask the user
        (recreate vs manual) before calling again with allow_rebuild=true."""
        if not remove_ids and not promote:
            raise ToolError("Nothing to apply: pass remove_ids and/or promote confirmed by the user.")
        app = provider.get()
        return await RefreshService(app, app.music_app).apply(
            playlist_id,
            purpose,
            remove_ids=remove_ids,
            promote=[t.model_dump() for t in promote or []],
            current_estimates=[t.model_dump() for t in current_estimates or []],
            allow_rebuild=allow_rebuild,
        )
