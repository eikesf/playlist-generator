"""Guide §7.8-7.9: dislikes are suggestions with a reason; the blacklist is explicit and enforced."""

from __future__ import annotations

from typing import Any

from mcp import Client

from apple_music_mcp.config import Credentials
from apple_music_mcp.container import AppContainer
from apple_music_mcp.resolver import resolve_tracks
from apple_music_mcp.server import build_server
from apple_music_mcp.sessions import CurationSessions, PreviewTrack, track_info

from .conftest import build_app
from .fake_apple import FakeAppleMusic


async def test_dislike_never_removes_or_blocks(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    pl = await app.library.create_playlist("Set")
    await app.library.add_tracks(pl.id, ["1001", "1002"])
    song = (await app.catalog.get_songs(["1001"]))[0]
    fid = app.feedback.dislike(track_info(song), "vocal irritante", playlist_id=pl.id)
    assert fake_apple.playlists[pl.id]["tracks"] == ["1001", "1002"]  # nothing removed
    assert app.blacklist().blocked_reason("1002", song.artist, "Other") is None  # artist not blocked
    assert app.feedback.list(open_only=True)[0]["reason"] == "vocal irritante"
    assert app.feedback.resolve(fid, "kept")
    assert app.feedback.list(open_only=True) == []


async def test_blacklisted_artist_is_kept_out_everywhere(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    banned = fake_apple.songs_by_id["2001"]
    entry = app.blacklist_entries.add_artist(banned.artist, "nunca mais coloca " + banned.artist)

    report = await resolve_tracks(app.catalog, [f"{banned.artist} - {banned.name}"])
    data = await app.discovery.annotate(report)
    assert data["tracks"][0]["blocked"] == "artist on the blacklist"
    assert data["summary"]["blocked"] == 1

    preview = await CurationSessions(app).preview(None, "Mix", [PreviewTrack("2001"), PreviewTrack("4001")])
    assert preview["track_count"] == 1 and preview["blocked_by_blacklist"][0]["song_id"] == "2001"

    assert app.blacklist_entries.lift(entry)
    assert app.blacklist().blocked_reason("2001", banned.artist, banned.name) is None


async def test_blacklisted_track_only_blocks_that_track(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    song = (await app.catalog.get_songs(["1001"]))[0]
    app.blacklist_entries.add_track(track_info(song), "tira essa música pra sempre")
    bl = app.blacklist()
    assert bl.blocked_reason("1001", song.artist, song.name) == "track on the blacklist"
    other = fake_apple.songs_by_id["1002"]
    assert bl.blocked_reason("1002", other.artist, other.name) is None


async def test_feedback_tools_via_mcp(fake_apple: FakeAppleMusic) -> None:
    creds = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    async with Client(build_server(lambda: build_app(creds))) as c:

        async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            r = await c.call_tool(name, args)
            assert not r.is_error, r.content
            data = r.structured_content
            assert isinstance(data, dict)
            return data

        d = await call("dislike_track", {"song_id": "1001", "reason": "muito lenta"})
        assert "NOT blocked" in d["note"]
        await call("blacklist_add", {"scope": "track", "value": "4001", "owner_request": "nunca mais"})
        listed = await call("blacklist_list", {})
        assert listed["count"] == 1 and listed["entries"][0]["owner_request"] == "nunca mais"
        created = await call("create_playlist", {"name": "X", "song_ids": ["4001", "4002"]})
        assert created["blocked_by_blacklist"] == {"4001": "track on the blacklist"}
        assert created["tracks_added"] == 1
        await call("blacklist_remove", {"blacklist_id": listed["entries"][0]["blacklist_id"]})
        assert (await call("blacklist_list", {}))["count"] == 0
