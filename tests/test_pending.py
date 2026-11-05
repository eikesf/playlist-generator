"""Expired login never forces an export: the playlist is kept and created after `login`."""

from __future__ import annotations

from mcp import Client

from apple_music_mcp.commands.pending import run_finish_pending
from apple_music_mcp.config import Credentials, save_credentials
from apple_music_mcp.server import build_server

from .conftest import build_app


async def test_expired_login_keeps_playlist_pending_then_finishes(fake_apple):
    expired = Credentials(media_user_token="expired-token-xxxxxxxxxxxx", storefront="br")
    async with Client(build_server(lambda: build_app(expired))) as c:
        res = await c.call_tool(
            "create_playlist", {"name": "Warehouse", "description": "set", "song_ids": ["4001", "1001"]}
        )
        tools = {t.name for t in (await c.list_tools()).tools}
    assert res.is_error and "AUTH" in res.content[0].text and "pending" in res.content[0].text
    assert "export_playlist_text" not in tools
    assert fake_apple.playlists == {}

    fresh = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    async with Client(build_server(lambda: build_app(fresh))) as c:
        done = (await c.call_tool("finish_pending_playlists", {})).structured_content
        again = (await c.call_tool("finish_pending_playlists", {})).structured_content
    [created] = done["created"]
    assert created["playlist"]["name"] == "Warehouse"
    assert fake_apple.playlists[created["playlist"]["id"]]["tracks"] == ["4001", "1001"]
    assert again["note"] == "No pending playlists."


async def test_discard_pending(fake_apple):
    expired = Credentials(media_user_token="expired-token-xxxxxxxxxxxx")
    async with Client(build_server(lambda: build_app(expired))) as c:
        await c.call_tool("create_playlist", {"name": "Old idea", "song_ids": ["4001"]})
    fresh = Credentials(media_user_token=fake_apple.user_token)
    async with Client(build_server(lambda: build_app(fresh))) as c:
        res = (await c.call_tool("finish_pending_playlists", {"discard": ["Old idea"]})).structured_content
    assert res["discarded"] == ["Old idea"] and res["created"] == []
    assert fake_apple.playlists == {}


async def test_finish_pending_cli(fake_apple):
    expired = Credentials(media_user_token="expired-token-xxxxxxxxxxxx")
    async with Client(build_server(lambda: build_app(expired))) as c:
        await c.call_tool("create_playlist", {"name": "Run", "song_ids": ["2001"]})
    save_credentials(Credentials(media_user_token=fake_apple.user_token, storefront="br"))
    lines: list[str] = []
    assert await run_finish_pending(build=build_app, out=lines.append) == 0
    assert lines[0].startswith("Created 'Run' with 1 tracks")
    lines.clear()
    assert await run_finish_pending(build=build_app, out=lines.append) == 0
    assert lines == ["No pending playlists."]
