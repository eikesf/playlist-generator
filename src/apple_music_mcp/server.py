"""MCP server exposing Apple Music catalog + library tools to Claude (stdio transport)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from rapidfuzz import fuzz

from . import __version__
from .client import AppleMusicClient, AppleMusicError, AuthError
from .config import credentials_path
from .resolver import normalize, resolve_tracks
from .tokens import TokenScrapeError

INSTRUCTIONS = """\
Apple Music tools for curating playlists and creating them directly in the user's library.
Typical flow: (1) propose tracks, (2) verify them with `resolve_tracks` (batch, one line per track,
format "Artist1, Artist2 - Title (Remix)"), (3) order them, (4) `create_playlist` with the
`song_ids_in_order` (or pass `tracks` and it resolves for you). Never invent catalog ids. If a tool
reports an auth problem, tell the user to run `apple-music-mcp login` on their Mac.
"""

ClientFactory = Callable[[], AppleMusicClient | Awaitable[AppleMusicClient]]


class _ClientHolder:
    def __init__(self, factory: ClientFactory):
        self._factory = factory
        self._client: AppleMusicClient | None = None

    async def get(self) -> AppleMusicClient:
        if self._client is None:
            made = self._factory()
            self._client = made if isinstance(made, AppleMusicClient) else await made
        return self._client


def _tool_error(exc: Exception) -> ToolError:
    if isinstance(exc, AuthError):
        return ToolError(f"AUTH: {exc}")
    if isinstance(exc, TokenScrapeError):
        return ToolError(f"AUTH: {exc}")
    if isinstance(exc, AppleMusicError):
        return ToolError(f"Apple Music error (HTTP {exc.status}): {exc}. Body: {str(exc.body)[:300]}")
    return ToolError(f"{exc.__class__.__name__}: {exc}")


def build_server(client_factory: ClientFactory | None = None, export_dir: Path | None = None) -> MCPServer:
    holder = _ClientHolder(client_factory or AppleMusicClient)
    exports = export_dir or Path.home() / "Music" / "Playlist Curator"
    mcp = MCPServer("apple-music", instructions=INSTRUCTIONS, version=__version__)

    @mcp.tool()
    async def auth_status() -> dict[str, Any]:
        """Check Apple Music credentials: developer token, user token and the account storefront.

        Call this first in a session. If `ready_to_create_playlists` is false, follow `next_step`.
        """
        client = await holder.get()
        result: dict[str, Any] = {
            "credentials_file": str(credentials_path()),
            "credentials": client.credentials.redacted(),
            "catalog_search": False,
            "ready_to_create_playlists": False,
        }
        try:
            await client.developer_token()
            result["catalog_search"] = True
        except Exception as exc:  # noqa: BLE001 - report, don't crash
            result["developer_token_error"] = str(exc)
        if not client.has_user_token:
            result["next_step"] = "Run `apple-music-mcp login` on the Mac to store the media-user-token."
            return result
        try:
            storefront = await client.user_storefront()
            result["account_storefront"] = storefront
            result["ready_to_create_playlists"] = True
            if storefront != client.storefront:
                result["warning"] = (
                    f"Configured storefront '{client.storefront}' differs from the account's '{storefront}'. "
                    "Searches use the configured one; run `apple-music-mcp login` to fix."
                )
        except Exception as exc:  # noqa: BLE001
            result["user_token_error"] = str(exc)
            result["next_step"] = "The user token is invalid or expired. Run `apple-music-mcp login` again."
        return result

    @mcp.tool()
    async def search_songs(query: str, limit: int = 10) -> dict[str, Any]:
        """Search the Apple Music catalog for songs. `query` is free text, e.g. "Amelie Lens Feel It".

        Use one artist per query. Max 25 results.
        """
        client = await holder.get()
        try:
            songs = await client.search_songs(query, limit=limit)
        except Exception as exc:
            raise _tool_error(exc) from exc
        return {"query": query, "count": len(songs), "songs": [s.to_dict() for s in songs]}

    @mcp.tool()
    async def artist_top_songs(artist: str, limit: int = 10) -> dict[str, Any]:
        """Return an artist's most popular songs in the catalog (best way to sample an artist's style)."""
        client = await holder.get()
        try:
            artists = await client.search_artists(artist, limit=5)
            best = max(artists, key=lambda a: fuzz.ratio(normalize(a.name), normalize(artist)), default=None)
            if best and fuzz.ratio(normalize(best.name), normalize(artist)) >= 80:
                songs = await client.artist_top_songs(best.id, limit=limit)
                return {
                    "artist": best.to_dict(),
                    "count": len(songs),
                    "songs": [s.to_dict() for s in songs],
                }
        except (AuthError, TokenScrapeError) as exc:
            raise _tool_error(exc) from exc
        except AppleMusicError:
            pass  # fall through to plain song search
        try:
            songs = await client.search_songs(artist, limit=25)
        except Exception as exc:
            raise _tool_error(exc) from exc
        own = [s for s in songs if fuzz.token_set_ratio(normalize(artist), normalize(s.artist)) >= 85]
        return {
            "artist": {"name": artist, "note": "artist page not found; results come from song search"},
            "count": len(own[:limit]),
            "songs": [s.to_dict() for s in own[:limit]],
        }

    @mcp.tool(name="resolve_tracks")
    async def resolve_tracks_tool(tracks: list[str]) -> dict[str, Any]:
        """Verify a list of tracks against the Apple Music catalog, in order.

        Each item: "Artist1, Artist2 - Title (Remix)". Returns per-track status (matched /
        low_confidence / not_found), the catalog `song_ids_in_order` for matched tracks, duplicates
        removed and the total duration. Only use ids from `matched` (or low_confidence after review).
        """
        client = await holder.get()
        try:
            report = await resolve_tracks(client, list(tracks))
        except Exception as exc:
            raise _tool_error(exc) from exc
        return report.to_dict()

    @mcp.tool()
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
        """
        if not name.strip():
            raise ToolError("name is required")
        client = await holder.get()
        report_dict: dict[str, Any] | None = None
        ids = [str(i) for i in (song_ids or [])]
        try:
            if tracks:
                report = await resolve_tracks(client, list(tracks))
                report_dict = report.to_dict()
                accepted = {"matched", "low_confidence"} if include_low_confidence else {"matched"}
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
            playlist = await client.create_playlist(name.strip(), description.strip() or None)
            added = await client.add_tracks(playlist.id, ids)
        except ToolError:
            raise
        except Exception as exc:
            raise _tool_error(exc) from exc
        result: dict[str, Any] = {
            "created": True,
            "playlist": playlist.to_dict(),
            "tracks_added": added,
            "note": "Library playlists sync to iPhone/Mac via iCloud Music Library within a few minutes.",
        }
        if report_dict:
            result["resolution_summary"] = report_dict["summary"]
            result["not_added"] = [
                t
                for t in report_dict["tracks"]
                if t["status"] != "matched"
                and not (include_low_confidence and t["status"] == "low_confidence")
            ]
        return result

    @mcp.tool()
    async def add_tracks_to_playlist(playlist_id: str, song_ids: list[str]) -> dict[str, Any]:
        """Append catalog songs (ids from `resolve_tracks`) to an existing library playlist (id `p.xxxx`)."""
        client = await holder.get()
        try:
            added = await client.add_tracks(playlist_id, [str(i) for i in song_ids])
        except Exception as exc:
            raise _tool_error(exc) from exc
        return {"playlist_id": playlist_id, "tracks_added": added}

    @mcp.tool()
    async def list_playlists(limit: int = 50) -> dict[str, Any]:
        """List playlists in the user's Apple Music library (id, name, url)."""
        client = await holder.get()
        try:
            playlists = await client.list_playlists(limit=limit)
        except Exception as exc:
            raise _tool_error(exc) from exc
        return {"count": len(playlists), "playlists": [p.to_dict() for p in playlists]}

    @mcp.tool()
    async def get_playlist_tracks(playlist_id: str) -> dict[str, Any]:
        """List the tracks of a library playlist in order (to verify a creation or plan a follow-up)."""
        client = await holder.get()
        try:
            items = await client.playlist_tracks(playlist_id)
        except Exception as exc:
            raise _tool_error(exc) from exc
        return {"playlist_id": playlist_id, "count": len(items), "tracks": items}

    @mcp.tool()
    async def export_playlist_text(name: str, tracks: list[str]) -> dict[str, Any]:
        """Fallback when the library is unreachable: save tracks as a .txt (one "Artist - Title" per line)
        ready for Tune My Music → Apple Music. Returns the file path and the text."""
        safe = re.sub(r"[^\w\- ]+", "", name).strip() or "playlist"
        exports.mkdir(parents=True, exist_ok=True)
        path = exports / f"{safe}.txt"
        text = "\n".join(t.strip() for t in tracks if t.strip()) + "\n"
        path.write_text(text, encoding="utf-8")
        return {"path": str(path), "lines": text.count("\n"), "text": text}

    return mcp


def main() -> None:
    build_server().run("stdio")
