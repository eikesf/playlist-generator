"""Weekly Discoveries (guide §7.11): Claude chooses, deterministic code validates and fills the playlist."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from mcp import Client

from apple_music_mcp.config import Credentials
from apple_music_mcp.container import AppContainer
from apple_music_mcp.db.history import ManagedPlaylistRepository, ProposalRepository
from apple_music_mcp.local_music import MusicAppReader
from apple_music_mcp.server import build_server
from apple_music_mcp.weekly_discoveries import WEEKLY_SUFFIX, WeeklyDiscoveries

from .conftest import build_app, result_text
from .fake_apple import FakeAppleMusic, FakeSong
from .test_refresh import MAIN, managed_main

NEW_IDS = [f"77{n:03d}" for n in range(36)]


def add_new_songs(fake: FakeAppleMusic) -> None:
    """36 catalog songs by artists the user never heard (enough for a ~30-track batch)."""
    for n, sid in enumerate(NEW_IDS):
        fake.catalog.append(FakeSong(sid, f"Fresh Cut {n}", f"Newcomer {n % 12}", "Fresh", 300000 + n))


async def enabled_main(app: AppContainer, name: str = "Warehouse") -> str:
    pl = await managed_main(app, name)
    ManagedPlaylistRepository(app.db).upsert(pl.id, origin="adopted", discoveries_enabled=True)
    return pl.id


async def test_valid_batch_fills_the_weekly_playlist_and_is_recorded(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    add_new_songs(fake_apple)
    pid = await enabled_main(app)
    result = await WeeklyDiscoveries(app).submit(pid, NEW_IDS[:30], notes="dark, hypnotic picks")

    weekly = result["weekly_playlist"]
    assert weekly["name"] == "Warehouse" + WEEKLY_SUFFIX
    assert fake_apple.playlists[weekly["id"]]["tracks"] == NEW_IDS[:30]
    assert fake_apple.playlists[pid]["tracks"] == MAIN  # the main playlist is never touched
    assert result["accepted"] == 30 and result["dropped"] == []
    managed = ManagedPlaylistRepository(app.db).get(pid)
    assert managed and managed["discoveries_apple_playlist_id"] == weekly["id"]
    [p] = ProposalRepository(app.db).find(statuses=("applied",), kind="discoveries")
    assert p["managed_playlist_id"] == managed["id"] and p["params"]["notes"] == "dark, hypnotic picks"

    # Next week: same playlist, last week's batch replaced by the new one.
    second = await WeeklyDiscoveries(app).submit(pid, NEW_IDS[6:36])
    assert second["weekly_playlist"]["id"] == weekly["id"]
    assert fake_apple.playlists[weekly["id"]]["tracks"] == NEW_IDS[6:36]
    assert sum(1 for p in fake_apple.playlists.values() if p["name"].endswith(WEEKLY_SUFFIX)) == 1


async def test_validation_drops_each_bad_track_with_its_reason(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    add_new_songs(fake_apple)
    pid = await enabled_main(app)
    app.blacklist_entries.add_artist("Newcomer 0", "nunca mais Newcomer 0")
    fake_apple.ratings[NEW_IDS[1]] = -1
    batch = [*NEW_IDS[:30], NEW_IDS[2], "4001", "2001", "999999", "5002"]
    result = await WeeklyDiscoveries(app).submit(pid, batch)
    reasons = {d["song_id"]: d["reason"] for d in result["dropped"]}
    assert reasons["4001"] == "in_main_playlist"
    assert reasons["2001"] == "already_heard"  # in the library and loved: not a discovery
    assert reasons["999999"] == "not_in_catalog"
    assert reasons[NEW_IDS[0]] == "blacklisted" and reasons[NEW_IDS[12]] == "blacklisted"
    assert reasons[NEW_IDS[1]] == "disliked"
    assert reasons["5002"] in ("in_main_playlist", "disliked")
    weekly = fake_apple.playlists[result["weekly_playlist"]["id"]]["tracks"]
    assert len(weekly) == len(set(weekly)) == result["accepted"]
    assert not set(weekly) & ({NEW_IDS[0], NEW_IDS[1], NEW_IDS[12], NEW_IDS[24]} | set(MAIN))


async def test_batch_outside_the_size_range_writes_nothing(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    add_new_songs(fake_apple)
    pid = await enabled_main(app)
    before = dict(fake_apple.playlists)
    with pytest.raises(ValueError, match="between 20 and 40"):
        await WeeklyDiscoveries(app).submit(pid, NEW_IDS[:12])
    assert fake_apple.playlists == before
    assert ProposalRepository(app.db).find(statuses=("applied",), kind="discoveries") == []


async def test_requires_a_managed_playlist_with_discoveries_on(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await managed_main(app)
    with pytest.raises(ValueError, match="discoveries_enabled"):
        await WeeklyDiscoveries(app).submit(pl.id, NEW_IDS[:30])


async def test_reuses_a_legacy_descobertas_playlist(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    add_new_songs(fake_apple)
    pid = await enabled_main(app)
    legacy = await app.library.create_playlist("Warehouse · Descobertas", "old staging")
    result = await WeeklyDiscoveries(app).submit(pid, NEW_IDS[:30])
    assert result["weekly_playlist"]["id"] == legacy.id


async def test_context_gives_claude_what_it_needs_without_writing(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    add_new_songs(fake_apple)
    pid = await enabled_main(app)
    await managed_main(app, "Off")
    app.blacklist_entries.add_artist("Anyma", "nunca mais Anyma")
    writes = [c for c in fake_apple.calls if c[0] != "GET"]
    listing = await WeeklyDiscoveries(app).context(None)
    assert [p["playlist_id"] for p in listing["playlists"]] == [pid]
    ctx = await WeeklyDiscoveries(app).context(pid)
    assert [c for c in fake_apple.calls if c[0] != "GET"] == writes
    assert ctx["playlist"]["name"] == "Warehouse" and ctx["profile"]["key"] == "dj_set"
    assert ctx["target_size"] == 30 and ctx["size_range"] == [20, 40]
    assert ctx["top_artists"] and "Anyma" in {b["label"] for b in ctx["blacklist"]}
    pool_ids = {c["id"] for c in ctx["candidates"]["new_artists"]} | {
        c["id"] for c in ctx["candidates"]["deep_cuts"]
    }
    assert not pool_ids & set(MAIN) and not pool_ids & {"9001", "9002"}
    assert "submit_weekly_discoveries" in ctx["next"]


def factory(fake: FakeAppleMusic) -> Callable[[], AppContainer]:
    creds = Credentials(media_user_token=fake.user_token, storefront="br")

    def make() -> AppContainer:
        a = build_app(creds)
        a.music_app = MusicAppReader(system="Linux")
        return a

    return make


async def test_discoveries_tools_via_mcp(fake_apple: FakeAppleMusic) -> None:
    add_new_songs(fake_apple)
    async with Client(build_server(factory(fake_apple))) as c:
        pid = (await c.call_tool("create_playlist", {"name": "Set", "song_ids": MAIN})).structured_content[
            "playlist"
        ]["id"]
        await c.call_tool("adopt_playlist", {"playlist_id": pid, "name": "Set", "purpose": "dj_set"})
        off = await c.call_tool("submit_weekly_discoveries", {"playlist_id": pid, "song_ids": NEW_IDS[:30]})
        await c.call_tool("update_managed_playlist", {"playlist_id": pid, "discoveries_enabled": True})
        ctx: Any = (await c.call_tool("weekly_discoveries_context", {"playlist_id": pid})).structured_content
        done: Any = (
            await c.call_tool("submit_weekly_discoveries", {"playlist_id": pid, "song_ids": NEW_IDS[:30]})
        ).structured_content
    assert off.is_error and "discoveries_enabled" in result_text(off)
    assert ctx["playlist"]["playlist_id"] == pid
    assert done["accepted"] == 30
    assert fake_apple.playlists[done["weekly_playlist"]["id"]]["name"] == "Set" + WEEKLY_SUFFIX
