"""Launch the real server as a subprocess over stdio, the way Claude Desktop / Claude Code do."""

from __future__ import annotations

import sys

from mcp import Client, StdioServerParameters


async def test_stdio_server_legacy_handshake(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "apple_music_mcp"],
        env={"APPLE_MUSIC_MCP_CONFIG_DIR": str(tmp_path), "HOME": str(tmp_path)},
    )
    async with Client(params, mode="legacy") as c:
        assert c.server_info.name == "apple-music"
        tools = {t.name for t in (await c.list_tools()).tools}
        assert {"create_playlist", "resolve_tracks", "auth_status"} <= tools
        r = await c.call_tool("plan_playlist", {"purpose": "estudo", "duration_minutes": 90})
        assert not r.is_error and r.structured_content["purpose"] == "study"
        assert "export_playlist_text" not in tools  # everything stays inside Apple Music
