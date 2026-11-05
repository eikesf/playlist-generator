"""Catalog tools: account check, search, artist sampling, batch verification."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from ..config import credentials_path
from ..curation.familiarity import MixTargets
from ..resolver import resolve_tracks
from .base import ContainerProvider, tool_errors


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    @mcp.tool()
    async def auth_status() -> dict[str, Any]:
        """Check Apple Music credentials: developer token, user token and the account storefront.

        Call this first in a session. If `ready_to_create_playlists` is false, follow `next_step`.
        """
        app = provider.get()
        result: dict[str, Any] = {
            "credentials_file": str(credentials_path()),
            "credentials": app.credentials.redacted(),
            "catalog_search": False,
            "ready_to_create_playlists": False,
        }
        try:
            await app.tokens.get()
            result["catalog_search"] = True
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            result["developer_token_error"] = str(exc)
        if not app.has_user_token:
            result["next_step"] = "Run `apple-music-mcp login` on the Mac to store the media-user-token."
            return result
        try:
            storefront = await app.library.storefront()
        except Exception as exc:  # noqa: BLE001
            result["user_token_error"] = str(exc)
            result["next_step"] = "The user token is invalid or expired. Run `apple-music-mcp login` again."
            return result
        result["account_storefront"] = storefront
        result["ready_to_create_playlists"] = True
        if storefront != app.storefront():
            result["warning"] = (
                f"Configured storefront '{app.storefront()}' differs from the account's '{storefront}'. "
                "Run `apple-music-mcp login` to fix."
            )
        return result

    @mcp.tool()
    @tool_errors
    async def search_songs(query: str, limit: int = 10) -> dict[str, Any]:
        """Search the Apple Music catalog for songs. `query` is free text, e.g. "Amelie Lens Feel It".

        Use one artist per query. Max 25 results.
        """
        songs = await provider.get().catalog.search_songs(query, limit=limit)
        return {"query": query, "count": len(songs), "songs": [s.to_dict() for s in songs]}

    @mcp.tool()
    @tool_errors
    async def artist_top_songs(artist: str, limit: int = 10) -> dict[str, Any]:
        """Return an artist's most popular songs in the catalog (best way to sample an artist's style)."""
        found, songs = await provider.get().catalog.top_songs_for(artist, limit=limit)
        artist_info = (
            found.to_dict()
            if found
            else {"name": artist, "note": "artist page not found; results come from song search"}
        )
        return {"artist": artist_info, "count": len(songs), "songs": [s.to_dict() for s in songs]}

    @mcp.tool(name="resolve_tracks")
    @tool_errors
    async def resolve_tracks_tool(
        tracks: list[str], known_tracks_pct: float | None = None, known_artists_pct: float | None = None
    ) -> dict[str, Any]:
        """Verify a list of tracks against the Apple Music catalog, in order.

        Each item: "Artist1, Artist2 - Title (Remix)". Returns per-track status (matched /
        low_confidence / not_found), the catalog `song_ids_in_order` for matched tracks, duplicates
        removed and the total duration. Only use ids from `matched` (or low_confidence after review).
        When logged in, each match gets `familiarity`: known_track / known_artist_new_track /
        new_artist (from the user's library, recently played and top artists) and `disliked`/`loved`
        from their ratings; the summary gives the real known_tracks_pct and known_artists_pct. Pass the
        user's targets to get `summary.mix_check` (deltas + what to swap). Drop disliked tracks.
        """
        app = provider.get()
        report = await resolve_tracks(app.catalog, list(tracks))
        if app.has_user_token:
            targets = None
            if known_tracks_pct is not None or known_artists_pct is not None:
                kt = known_tracks_pct if known_tracks_pct is not None else 0
                targets = MixTargets(kt, known_artists_pct if known_artists_pct is not None else kt)
            return await app.discovery.annotate(report, targets)
        return report.to_dict()
