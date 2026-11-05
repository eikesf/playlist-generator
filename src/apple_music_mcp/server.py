"""MCP server entry point (stdio). Tools live in ``apple_music_mcp.tools``."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import __version__
from .tools import register_all
from .tools.base import ContainerFactory, ContainerProvider

INSTRUCTIONS = """\
Apple Music tools for curating playlists and creating them directly in the user's library.
Flow: (1) `plan_playlist` for the purpose (study, workout, dj_set, party...) and duration,
(2) propose tracks per act and verify them with `resolve_tracks`, (3) estimate BPM/energy per track
and sequence with `order_tracks`, (4) `create_playlist` with `song_ids_in_order`.
Never invent catalog ids. On an AUTH error, tell the user to run `apple-music-mcp login` on their Mac.
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
