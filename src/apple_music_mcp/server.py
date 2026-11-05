"""MCP server entry point (stdio). Tools live in ``apple_music_mcp.tools``."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import __version__
from .tools import register_all
from .tools.base import ContainerFactory, ContainerProvider

INSTRUCTIONS = """\
Apple Music tools for curating playlists and creating them directly in the user's library.
Typical flow: (1) propose tracks, (2) verify them with `resolve_tracks` (batch, one line per track,
format "Artist1, Artist2 - Title (Remix)"), (3) order them, (4) `create_playlist` with the
`song_ids_in_order` (or pass `tracks` and it resolves for you). Never invent catalog ids. If a tool
reports an auth problem, tell the user to run `apple-music-mcp login` on their Mac.
"""

DEFAULT_EXPORT_DIR = Path.home() / "Music" / "Playlist Curator"


def build_server(
    container_factory: ContainerFactory | None = None, export_dir: Path | None = None
) -> MCPServer:
    mcp = MCPServer("apple-music", instructions=INSTRUCTIONS, version=__version__)
    provider = ContainerProvider(container_factory) if container_factory else ContainerProvider()
    register_all(mcp, provider, export_dir or DEFAULT_EXPORT_DIR)
    return mcp


def main() -> None:
    build_server().run("stdio")
