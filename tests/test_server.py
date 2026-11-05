from __future__ import annotations

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
    "finish_pending_playlists",
    "list_playlist_profiles",
    "plan_playlist",
    "order_tracks",
    "reorder_playlist",
    "sync_playlist",
    "remove_tracks_from_playlist",
    "resequence_playlist",
    "rename_playlist",
    "insert_tracks_into_playlist",
    "my_taste_profile",
    "similar_artists",
    "discover_tracks",
    "lookup_bpm",
    "plan_playlist_refresh",
    "apply_playlist_refresh",
    "known_tracks",
    "deep_cuts",
}


@pytest.fixture
def server_factory(fake_apple, tmp_path):
    def make(user_token: str | None = "default"):
        token = fake_apple.user_token if user_token == "default" else user_token
        creds = Credentials(media_user_token=token, storefront="br")
        return build_server(lambda: build_app(creds))

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


async def test_plan_and_order_tools(server_factory):
    plan = (
        await call(server_factory(), "plan_playlist", {"purpose": "treino", "duration_minutes": 60})
    ).structured_content
    assert plan["purpose"] == "workout" and plan["total_tracks"] == 16
    tracks = [
        {"id": "2001", "artist": "Amelie Lens", "title": "Hypnotized", "bpm": 130, "energy": 9},
        {"id": "4001", "artist": "Stephan Bodzin", "title": "Singularity", "bpm": 122, "energy": 3},
        {"id": "1001", "artist": "Charlotte de Witte", "title": "Doppler", "bpm": 132, "energy": 8},
    ]
    result = (
        await call(server_factory(), "order_tracks", {"purpose": "dj_set", "tracks": tracks})
    ).structured_content
    assert result["song_ids_in_order"][0] == "4001"
    assert result["total_duration"] == "20:28"  # durations fetched from the catalog
    bad = await call(
        server_factory(), "order_tracks", {"purpose": "dj_set", "tracks": [{"id": "1", "energy": 99}]}
    )
    assert bad.is_error


async def test_editing_tools_via_mcp(server_factory, fake_apple):
    server = server_factory()
    async with Client(server) as c:
        created = (
            await c.call_tool("create_playlist", {"name": "Set", "song_ids": ["1001", "4001"]})
        ).structured_content
        pid = created["playlist"]["id"]
        tracks = (await c.call_tool("get_playlist_tracks", {"playlist_id": pid})).structured_content["tracks"]
        assert [t["id"] for t in tracks] == ["1001", "4001"]

        bad = await c.call_tool(
            "reorder_playlist", {"playlist_id": pid, "song_ids_in_order": ["4001", "2001"]}
        )
        assert bad.is_error and "same tracks" in bad.content[0].text

        ok = await c.call_tool(
            "reorder_playlist", {"playlist_id": pid, "song_ids_in_order": ["4001", "1001"]}
        )
        assert ok.structured_content["verified_order"] is True
        assert fake_apple.playlists[pid]["tracks"] == ["4001", "1001"]

        # Add two new tracks and let the engine re-sequence the whole set.
        new_set = [
            {"id": "4001", "artist": "Stephan Bodzin", "title": "Singularity", "bpm": 122, "energy": 3},
            {"id": "1001", "artist": "Charlotte de Witte", "title": "Doppler", "bpm": 134, "energy": 9.5},
            {"id": "2002", "artist": "Amelie Lens", "title": "Feel It", "bpm": 128, "energy": 6},
            {"id": "4002", "artist": "Stephan Bodzin", "title": "Strand", "bpm": 124, "energy": 5},
        ]
        res = await c.call_tool(
            "resequence_playlist", {"playlist_id": pid, "purpose": "dj_set", "tracks": new_set}
        )
        data = res.structured_content
        assert data["edit"]["verified_order"]
        assert fake_apple.playlists[pid]["tracks"] == data["sequence"]["song_ids_in_order"]
        assert fake_apple.playlists[pid]["tracks"][0] == "4001"
        assert set(fake_apple.playlists[pid]["tracks"]) == {"4001", "1001", "2002", "4002"}

        await c.call_tool("remove_tracks_from_playlist", {"playlist_id": pid, "song_ids": ["2002"]})
        assert "2002" not in fake_apple.playlists[pid]["tracks"]

        renamed = (
            await c.call_tool("rename_playlist", {"playlist_id": pid, "name": "Set v2"})
        ).structured_content
        assert renamed["playlist"]["name"] == "Set v2"
