"""MCP server entry point (stdio). Tools live in ``apple_music_mcp.tools``."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

from . import __version__
from .tools import register_all
from .tools.base import ContainerFactory, ContainerProvider

INSTRUCTIONS = """\
Apple Music tools for curating playlists and creating them directly in the user's library.
Read the `playlist-curator` prompt (or the `skill://playlist-curator` resource) for the full flow.
Session flow: ALWAYS ask first "edit an existing playlist or create a new one?", then `start_session`.
Ask as many questions as needed (purpose, mood, artists, two-level mix, duration: default 5 hours) and
save them with `record_answer`. Then `plan_playlist` -> gather tracks per familiarity bucket ->
`resolve_tracks` -> `order_tracks` -> `preview_playlist` (nothing is created) -> adjust with
`reject_preview_tracks` and a new preview -> `approve_preview` creates it. Create directly with
`create_playlist` only when the user asks to skip the preview. Playlist names and descriptions in English.
To ADD tracks to an existing playlist use `insert_tracks_into_playlist` (keeps the current order);
`remove_tracks_from_playlist` to remove; `resequence_playlist` only to reshuffle everything on request.
"Não gostei" = `dislike_track` with the reason (a suggestion, never removes or blocks the artist).
Blacklist only on explicit request (`blacklist_add`). Weekly refresh/promotion proposals wait for the
user: `list_pending_proposals` -> `show_proposal` -> `decide_proposal` (only what he confirms) ->
`apply_proposal`. Never invent catalog ids. On an AUTH error, tell the
user to run `apple-music-mcp login` on their Mac.
"""


def skill_text() -> str:
    """The playlist-curator skill, packaged in the wheel or read from the repository checkout."""
    here = Path(__file__).resolve().parent
    for candidate in (
        here / "skill" / "SKILL.md",
        here.parents[1] / "skill" / "playlist-curator" / "SKILL.md",
    ):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return INSTRUCTIONS


def build_server(container_factory: ContainerFactory | None = None) -> MCPServer:
    mcp = MCPServer("apple-music", instructions=INSTRUCTIONS, version=__version__)
    provider = ContainerProvider(container_factory) if container_factory else ContainerProvider()
    register_all(mcp, provider)

    # Guide §4: the behaviour instructions are served by the server itself, so they work the same
    # in Claude Desktop, Claude Code and through Dispatch on the iPhone (no separate skill upload).
    @mcp.prompt(
        name="playlist-curator",
        description="How to curate a playlist: edit or create, questions, mix, preview, approval.",
    )
    def playlist_curator_prompt() -> str:
        return skill_text()

    @mcp.resource(
        "skill://playlist-curator",
        name="playlist-curator",
        description="The playlist-curator skill (full curation flow for the apple-music tools).",
        mime_type="text/markdown",
    )
    def playlist_curator_resource() -> str:
        return skill_text()

    return mcp


def main() -> None:
    build_server().run("stdio")
