from __future__ import annotations

from pathlib import Path

import pytest
from mcp import Client

from apple_music_mcp.config import Credentials
from apple_music_mcp.server import build_server

from .conftest import build_app

EXPECTED_TOOLS = {
    "auth_status",
    "search_songs",
    "artist_top_songs",
    "resolve_tracks",
    "create_playlist",
    "add_tracks_to_playlist",
    "list_playlists",
    "get_playlist_tracks",
    "export_playlist_text",
}


@pytest.fixture
def server_factory(fake_apple, tmp_path):
    def make(user_token: str | None = "default"):
        token = fake_apple.user_token if user_token == "default" else user_token
        creds = Credentials(media_user_token=token, storefront="br")
        return build_server(lambda: build_app(creds), export_dir=tmp_path / "exports")

    return make


async def call(server, name, args=None):
    async with Client(server) as c:
        result = await c.call_tool(name, args or {})
    return result


async def test_lists_all_tools_with_descriptions(server_factory):
    async with Client(server_factory()) as c:
        tools = (await c.list_tools()).tools
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert all(t.description and len(t.description) > 20 for t in tools)


async def test_auth_status_ready(server_factory):
    r = await call(server_factory(), "auth_status")
    data = r.structured_content
    assert data["ready_to_create_playlists"] is True
    assert data["account_storefront"] == "br"
    assert "valid-media-user-token" not in str(data)  # secrets are masked


async def test_auth_status_without_user_token_explains_next_step(server_factory):
    data = (await call(server_factory(user_token=None), "auth_status")).structured_content
    assert data["catalog_search"] is True
    assert data["ready_to_create_playlists"] is False
    assert "apple-music-mcp login" in data["next_step"]


async def test_auth_status_with_expired_user_token(server_factory):
    data = (
        await call(server_factory(user_token="expired-token-xxxxxxxxxxxx"), "auth_status")
    ).structured_content
    assert data["ready_to_create_playlists"] is False
    assert "user_token_error" in data


async def test_search_songs_tool(server_factory):
    data = (
        await call(server_factory(), "search_songs", {"query": "Amelie Lens Feel It", "limit": 3})
    ).structured_content
    assert data["songs"][0]["name"] == "Feel It"
    assert data["songs"][0]["duration"] == "5:52"


async def test_artist_top_songs_tool_and_fallback(server_factory):
    data = (await call(server_factory(), "artist_top_songs", {"artist": "Stephan Bodzin"})).structured_content
    assert [s["name"] for s in data["songs"]] == ["Singularity", "Strand"]
    # Eric Prydz has no artist page in the fake: falls back to song search filtered by artist.
    data = (await call(server_factory(), "artist_top_songs", {"artist": "Eric Prydz"})).structured_content
    assert [s["name"] for s in data["songs"]] == ["Opus"]
    assert "note" in data["artist"]


async def test_create_playlist_requires_login(server_factory, fake_apple):
    r = await call(server_factory(user_token=None), "create_playlist", {"name": "X", "song_ids": ["1001"]})
    assert r.is_error
    assert "AUTH" in r.content[0].text and "apple-music-mcp login" in r.content[0].text
    assert fake_apple.playlists == {}


async def test_create_playlist_dry_run_creates_nothing(server_factory, fake_apple):
    r = await call(
        server_factory(),
        "create_playlist",
        {"name": "X", "tracks": ["Amelie Lens - Hypnotized"], "dry_run": True},
    )
    assert r.structured_content["would_add"] == 1
    assert fake_apple.playlists == {}


async def test_create_playlist_rejects_empty(server_factory):
    r = await call(
        server_factory(), "create_playlist", {"name": "X", "tracks": ["Nobody - Nothing At All Zzz"]}
    )
    assert r.is_error and "nothing matched" in r.content[0].text


async def test_export_playlist_text(server_factory, tmp_path):
    data = (
        await call(
            server_factory(), "export_playlist_text", {"name": "Set / 01", "tracks": ["A - B", " C - D "]}
        )
    ).structured_content
    assert data["path"].endswith("Set  01.txt") or data["path"].endswith("Set 01.txt")
    assert Path(data["path"]).read_text(encoding="utf-8") == "A - B\nC - D\n"
