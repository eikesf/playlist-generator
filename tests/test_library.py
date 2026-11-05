from __future__ import annotations

import pytest

from apple_music_mcp import client as client_mod
from apple_music_mcp.client import AppleMusicError


async def test_create_playlist_and_add_tracks_in_order(client, fake_apple):
    playlist = await client.create_playlist("Techno Set", "Builds like a DJ set")
    assert playlist.id.startswith("p.")
    assert playlist.url == f"https://music.apple.com/library/playlist/{playlist.id}"
    assert await client.add_tracks(playlist.id, ["4001", "1001", "2001"]) == 3
    assert fake_apple.playlists[playlist.id]["tracks"] == ["4001", "1001", "2001"]
    assert fake_apple.playlists[playlist.id]["description"] == "Builds like a DJ set"
    tracks = await client.playlist_tracks(playlist.id)
    assert [t["catalog_id"] for t in tracks] == ["4001", "1001", "2001"]


async def test_add_tracks_chunks_large_lists(client, fake_apple, monkeypatch):
    monkeypatch.setattr(client_mod, "ADD_TRACKS_CHUNK", 2)
    playlist = await client.create_playlist("Chunked")
    ids = ["1001", "1002", "1003", "2001", "2002"]
    assert await client.add_tracks(playlist.id, ids) == 5
    assert fake_apple.playlists[playlist.id]["tracks"] == ids
    posts = [c for c in fake_apple.calls if c == ("POST", f"/v1/me/library/playlists/{playlist.id}/tracks")]
    assert len(posts) == 3


async def test_add_more_than_100_tracks_is_split(client, fake_apple):
    from .fake_apple import FakeSong

    fake_apple.catalog += [FakeSong(str(90000 + i), f"Track {i}", "Bulk", "Bulk", 300000) for i in range(150)]
    playlist = await client.create_playlist("Long set")
    ids = [str(90000 + i) for i in range(150)]
    assert await client.add_tracks(playlist.id, ids) == 150
    assert fake_apple.playlists[playlist.id]["tracks"] == ids


async def test_list_and_delete_playlists(client, fake_apple):
    a = await client.create_playlist("A")
    await client.create_playlist("B")
    assert [p.name for p in await client.list_playlists()] == ["A", "B"]
    await client.delete_playlist(a.id)
    assert [p.name for p in await client.list_playlists()] == ["B"]


async def test_invalid_track_id_raises(client):
    playlist = await client.create_playlist("Bad")
    with pytest.raises(AppleMusicError) as err:
        await client.add_tracks(playlist.id, ["does-not-exist"])
    assert err.value.status == 400
