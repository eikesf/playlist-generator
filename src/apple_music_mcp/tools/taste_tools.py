"""Taste tools: the user's listening profile, similar artists and taste-aware discovery."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .base import ContainerProvider, tool_errors


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    @mcp.tool()
    @tool_errors
    async def my_taste_profile(refresh: bool = False) -> dict[str, Any]:
        """The user's real taste from Apple Music: top artists (library + recently played + heavy
        rotation), recently played tracks and library size. Use it before curating to pick reference
        artists, respect the known/new ratio and avoid over-played tracks. Cached for a few hours."""
        profile = await provider.get().taste.profile(refresh=refresh)
        return {
            "library_songs": len(profile.library_ids),
            "top_artists": profile.top_artists(25),
            "heavy_rotation_artists": profile.heavy_rotation_artists,
            "recently_played": [f"{s.artist} - {s.name}" for s in profile.recent[:30]],
        }

    @mcp.tool()
    @tool_errors
    async def similar_artists(artist: str, limit: int = 10) -> dict[str, Any]:
        """Artists Apple Music considers similar to `artist` (catalog data, not memory)."""
        app = provider.get()
        found = await app.catalog.find_artist(artist)
        if not found:
            raise ToolError(f"Artist '{artist}' not found in the catalog.")
        similar = await app.catalog.similar_artists(found.id, limit=limit)
        return {"artist": found.to_dict(), "count": len(similar), "similar": [a.to_dict() for a in similar]}

    @mcp.tool()
    @tool_errors
    async def discover_tracks(
        seed_artists: list[str],
        per_artist: int = 3,
        limit: int = 40,
        exclude_known: bool = True,
        exclude_artists: list[str] | None = None,
    ) -> dict[str, Any]:
        """Find NEW tracks for the user: top songs of artists similar to the seeds, skipping songs the
        user already knows (library / recently played) and songs they disliked. Each candidate has a
        ready `line` for resolve_tracks/order and `similar_to` (which seed led there). Use it to fill
        the "new" share of the known/new ratio with catalog-backed discoveries."""
        result = await provider.get().discovery.discover(
            seed_artists,
            per_artist=per_artist,
            limit=limit,
            exclude_known=exclude_known,
            exclude=exclude_artists,
        )
        return result.to_dict()

    @mcp.tool()
    @tool_errors
    async def known_tracks(artists: list[str], limit: int = 50) -> dict[str, Any]:
        """Songs the user ALREADY KNOWS (library / recently played) by these artists, loved ones first.
        Fills the 'known_track' share of the familiarity mix with the safest picks."""
        return await provider.get().discovery.known_tracks(artists, limit=limit)

    @mcp.tool()
    @tool_errors
    async def deep_cuts(artists: list[str], per_artist: int = 4) -> dict[str, Any]:
        """Songs by artists the user already listens to that they have NOT heard yet (most popular
        first, disliked excluded). Fills the 'known_artist_new_track' share of the familiarity mix."""
        return await provider.get().discovery.deep_cuts(artists, per_artist=per_artist)
