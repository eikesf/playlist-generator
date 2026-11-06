from __future__ import annotations

import pytest

from apple_music_mcp.apple.errors import AppleMusicError
from apple_music_mcp.container import AppContainer

from .fake_apple import FakeAppleMusic


async def test_create_playlist_and_add_tracks_in_order(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    playlist = await app.library.create_playlist("Techno Set", "Builds like a DJ set")
    assert playlist.id.startswith("p.")
    assert playlist.url == f"https://music.apple.com/library/playlist/{playlist.id}"
    assert await app.library.add_tracks(playlist.id, ["4001", "1001", "2001"]) == 3
    assert fake_apple.playlists[playlist.id]["tracks"] == ["4001", "1001", "2001"]
    assert fake_apple.playlists[playlist.id]["description"] == "Builds like a DJ set"
    entries = await app.library.playlist_tracks(playlist.id)
    assert [e.catalog_id for e in entries] == ["4001", "1001", "2001"]
    assert entries[0].duration_ms == 467000


async def test_add_tracks_chunks_large_lists(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    app.library.add_chunk = 2
    playlist = await app.library.create_playlist("Chunked")
    ids = ["1001", "1002", "1003", "2001", "2002"]
    assert await app.library.add_tracks(playlist.id, ids) == 5
    assert fake_apple.playlists[playlist.id]["tracks"] == ids
    posts = [c for c in fake_apple.calls if c == ("POST", f"/v1/me/library/playlists/{playlist.id}/tracks")]
    assert len(posts) == 3


async def test_add_more_than_100_tracks_is_split(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    from .fake_apple import FakeSong

    fake_apple.catalog += [FakeSong(str(90000 + i), f"Track {i}", "Bulk", "Bulk", 300000) for i in range(150)]
    playlist = await app.library.create_playlist("Long set")
    ids = [str(90000 + i) for i in range(150)]
    assert await app.library.add_tracks(playlist.id, ids) == 150
    assert fake_apple.playlists[playlist.id]["tracks"] == ids


async def test_list_get_update_and_delete_playlists(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    a = await app.library.create_playlist("A")
    await app.library.create_playlist("B")
    assert [p.name for p in await app.library.list_playlists()] == ["A", "B"]
    await app.library.update_playlist(a.id, name="A2", description="new")
    got = await app.library.get_playlist(a.id)
    assert (got.name, got.description) == ("A2", "new")
    await app.library.delete_playlist(a.id)
    assert [p.name for p in await app.library.list_playlists()] == ["B"]


@pytest.mark.parametrize("variant", ["query", "path"])
async def test_remove_entry_learns_the_accepted_request_shape(
    app: AppContainer, fake_apple: FakeAppleMusic, variant: str
) -> None:
    fake_apple.remove_variants = {variant}
    playlist = await app.library.create_playlist("R")
    await app.library.add_tracks(playlist.id, ["1001", "2001", "1001"])
    await app.library.remove_entry(playlist.id, "i.1001")
    assert fake_apple.playlists[playlist.id]["tracks"] == ["2001"]  # all occurrences removed
    assert app.library._remove_variant == variant


async def test_remove_entry_raises_when_unsupported(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    fake_apple.remove_variants = set()
    playlist = await app.library.create_playlist("R")
    await app.library.add_tracks(playlist.id, ["1001"])
    with pytest.raises(AppleMusicError):
        await app.library.remove_entry(playlist.id, "i.1001")


async def test_invalid_track_id_raises(app: AppContainer) -> None:
    playlist = await app.library.create_playlist("Bad")
    with pytest.raises(AppleMusicError) as err:
        await app.library.add_tracks(playlist.id, ["does-not-exist"])
    assert err.value.status == 400
