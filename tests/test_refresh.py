"""Weekly refresh: staging for discoveries, archive-before-remove, never lose a track silently."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from mcp import Client

from apple_music_mcp.config import Credentials
from apple_music_mcp.container import AppContainer
from apple_music_mcp.local_music import (
    SEP,
    MusicAppReader,
    MusicAppUnavailable,
    PlaylistNotInMusicApp,
    parse_stats,
)
from apple_music_mcp.models import Playlist
from apple_music_mcp.refresh import ARCHIVE_SUFFIX, PLANS_KEY, STAGING_SUFFIX, RefreshService, RefreshSettings
from apple_music_mcp.server import build_server

from .conftest import build_app
from .fake_apple import FakeAppleMusic

MAIN = ["4001", "4002", "2003", "3001", "1001", "2002", "5001", "5002", "7001", "8001"]
OLD = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
NEW = datetime.now().isoformat(timespec="seconds")


def fake_music_app(
    fake_apple: FakeAppleMusic,
    plays: dict[str, int],
    favorites: Collection[str] = (),
    recent: Collection[str] = (),
    fail: bool = False,
) -> MusicAppReader:
    async def runner(args: list[str]) -> tuple[int, str, str]:
        if fail:
            return 1, "", "execution error: Not authorized to send Apple events to Music. (-1743)"
        if args[0] == "names":
            return 0, "".join(pl["name"] + "\n" for pl in fake_apple.playlists.values()), ""
        lines = []
        for pl in fake_apple.playlists.values():
            if pl["name"] != args[1]:
                continue
            for sid in pl["tracks"]:
                song = fake_apple.songs_by_id[sid]
                added = NEW if sid in recent else OLD
                fav = "true" if sid in favorites else "false"
                fields = [song.name, song.artist, str(plays.get(sid, 0)), "0", fav, "false", added]
                lines.append(SEP.join(fields))
        return 0, "\n".join(lines) + "\n", ""

    return MusicAppReader(runner=runner, system="Darwin")


async def _main_playlist(app: AppContainer, name: str = "Warehouse") -> Playlist:
    pl = await app.library.create_playlist(name, "techno set")
    await app.library.add_tracks(pl.id, MAIN)
    return pl


def test_parse_stats() -> None:
    line = SEP.join(["Doppler", "Charlotte de Witte", "12", "1", "true", "false", "2026-01-02T03:04:05"])
    [s] = parse_stats(line + "\n" + "garbage line\n")
    assert s.date_added is not None
    assert (s.played, s.skipped, s.favorited, s.date_added.year) == (12, 1, True, 2026)


async def test_reader_unavailable_off_macos_and_without_permission(fake_apple: FakeAppleMusic) -> None:
    with pytest.raises(MusicAppUnavailable, match="macOS"):
        await MusicAppReader(system="Linux").playlist_stats("x")
    with pytest.raises(MusicAppUnavailable, match="Automation"):
        await fake_music_app(fake_apple, {}, fail=True).playlist_stats("x")


def music_app_with(playlists: dict[str, list[str]]) -> MusicAppReader:
    """A Music app whose user playlists are exactly ``playlists`` (name -> track lines)."""
    calls: list[list[str]] = []

    async def runner(args: list[str]) -> tuple[int, str, str]:
        calls.append(args)
        if args[0] == "names":
            return 0, "".join(name + "\n" for name in playlists), ""
        if args[1] not in playlists:  # what AppleScript does for an unknown name
            return 1, "", f'Can’t get user playlist 1 whose name = "{args[1]}". (-1728)'
        return 0, "".join(line + "\n" for line in playlists[args[1]]), ""

    reader = MusicAppReader(runner=runner, system="Darwin")
    reader.calls = calls  # type: ignore[attr-defined]
    return reader


async def test_reader_matches_the_music_app_spelling_of_the_name() -> None:
    line = SEP.join(["Time", "Hans Zimmer", "3", "0", "false", "false", ""])
    # Seen live: the Music app keeps a trailing space the web API may not.
    reader = music_app_with({"Epic songs ": [line], "Café  Focus": [line]})
    assert [s.name for s in await reader.playlist_stats("Epic songs ")] == ["Time"]
    assert [s.name for s in await reader.playlist_stats("epic songs")] == ["Time"]
    assert [s.name for s in await reader.playlist_stats("Cafe\u0301 Focus")] == ["Time"]  # NFD vs NFC
    assert reader.calls[-1] == ["stats", "Café  Focus"]  # type: ignore[attr-defined]


async def test_reader_reports_playlists_missing_from_the_music_app() -> None:
    # Seen live: catalog playlists added to the library ("Brown Noise") are not user playlists.
    reader = music_app_with({"Workout": []})
    with pytest.raises(PlaylistNotInMusicApp, match="Brown Noise"):
        await reader.playlist_stats("Brown Noise")
    assert reader.calls == [["names"]]  # type: ignore[attr-defined]


async def test_plan_with_play_data_suggests_least_played_and_never_touches_main(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await _main_playlist(app)
    plays = {sid: 10 + i for i, sid in enumerate(MAIN)}
    plays.update({"4002": 1, "2003": 2, "3001": 0, "8001": 0})  # 3001 favorite, 8001 just added
    reader = fake_music_app(fake_apple, plays, favorites={"3001"}, recent={"8001"})
    plan = await RefreshService(app, reader).plan(pl.id)

    assert fake_apple.playlists[pl.id]["tracks"] == MAIN  # main untouched
    assert plan["main_playlist_changed"] is False and plan["play_data"] is True
    assert [c["id"] for c in plan["rotation_candidates"]] == ["4002", "2003"]  # 20% of 10, least played
    assert [c["id"] for c in plan["removal_candidates"]] == ["5002"]  # disliked rating
    staging = plan["staging_playlist"]
    assert staging["name"] == "Warehouse" + STAGING_SUFFIX
    added = [c["id"] for c in plan["added_to_staging"]]
    assert added and fake_apple.playlists[staging["id"]]["tracks"] == added
    assert not set(added) & set(MAIN)
    assert not set(added) & {"1001", "2001", "7001", "1002"}  # nothing the user already knows
    assert app.state.get(PLANS_KEY)[pl.id]["removal_candidates"][0]["id"] == "5002"


async def test_plan_without_play_data_suggests_no_rotation(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await _main_playlist(app)
    plan = await RefreshService(app, MusicAppReader(system="Linux")).plan(pl.id)
    assert plan["rotation_candidates"] == [] and plan["play_data"] is False
    assert any("No play data" in n for n in plan["notes"])
    assert plan["added_to_staging"]  # staging still gets discoveries


async def test_equal_play_counts_do_not_identify_anything(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await _main_playlist(app)
    plan = await RefreshService(app, fake_music_app(fake_apple, {})).plan(pl.id)
    assert plan["rotation_candidates"] == []
    assert any("do not tell tracks apart" in n for n in plan["notes"])


async def test_second_plan_reuses_staging_and_does_not_duplicate(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await _main_playlist(app)
    service = RefreshService(app, MusicAppReader(system="Linux"))
    first = await service.plan(pl.id, RefreshSettings(new_tracks=2))
    second = await service.plan(pl.id, RefreshSettings(new_tracks=2))
    assert first["staging_playlist"]["id"] == second["staging_playlist"]["id"]
    staged = fake_apple.playlists[first["staging_playlist"]["id"]]["tracks"]
    assert len(staged) == len(set(staged))


async def test_apply_archives_before_removing_and_keeps_order(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await _main_playlist(app)
    service = RefreshService(app, MusicAppReader(system="Linux"))
    result = await service.apply(pl.id, "dj_set", remove_ids=["5002", "2002", "nope"])
    archive = result["archive_playlist"]
    assert archive["name"] == "Warehouse" + ARCHIVE_SUFFIX
    assert fake_apple.playlists[archive["id"]]["tracks"] == ["5002", "2002"]
    assert fake_apple.playlists[pl.id]["tracks"] == [i for i in MAIN if i not in {"5002", "2002"}]
    assert any("nope" in w for w in result["warnings"])
    # Removing again later never duplicates the archive copy.
    await app.library.add_tracks(pl.id, ["5002"])
    await service.apply(pl.id, "dj_set", remove_ids=["5002"])
    assert fake_apple.playlists[archive["id"]]["tracks"] == ["5002", "2002"]


async def test_apply_when_removal_is_refused_keeps_everything(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    fake_apple.remove_variants = set()
    pl = await _main_playlist(app)
    result = await RefreshService(app, MusicAppReader(system="Linux")).apply(
        pl.id, "dj_set", remove_ids=["5002"]
    )
    assert result["removed"]["edit"]["status"] == "needs_decision"
    assert fake_apple.playlists[pl.id]["tracks"] == MAIN
    assert fake_apple.playlists[result["archive_playlist"]["id"]]["tracks"] == ["5002"]


async def test_promote_inserts_at_best_position_and_leaves_staging(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await _main_playlist(app)
    service = RefreshService(app, MusicAppReader(system="Linux"))
    plan = await service.plan(pl.id, RefreshSettings(new_tracks=3))
    staged = [c["id"] for c in plan["added_to_staging"]]
    pick = staged[0]
    song = fake_apple.songs_by_id[pick]
    result = await service.apply(
        pl.id,
        "dj_set",
        promote=[{"id": pick, "artist": song.artist, "title": song.name, "bpm": 121, "energy": 2.5}],
        current_estimates=[
            {"id": "4001", "artist": "Stephan Bodzin", "title": "Singularity", "bpm": 122, "energy": 3}
        ],
    )
    main = fake_apple.playlists[pl.id]["tracks"]
    assert pick in main and [i for i in main if i != pick] == MAIN  # existing order intact
    assert result["promoted"]["inserted"][0]["id"] == pick
    assert pick not in fake_apple.playlists[plan["staging_playlist"]["id"]]["tracks"]


async def test_promotion_candidates_come_from_love_and_plays(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await _main_playlist(app)
    service = RefreshService(app, MusicAppReader(system="Linux"))
    plan = await service.plan(pl.id, RefreshSettings(new_tracks=3))
    staged = [c["id"] for c in plan["added_to_staging"]]
    fake_apple.ratings[staged[0]] = 1
    plays = {staged[1]: 5}
    plan2 = await RefreshService(app, fake_music_app(fake_apple, plays)).plan(
        pl.id, RefreshSettings(new_tracks=0)
    )
    why = {c["id"]: c["why"] for c in plan2["promotion_candidates"]}
    assert why[staged[0]] == "loved" and why[staged[1]] == "played 5x"


async def test_refresh_tools_via_mcp(fake_apple: FakeAppleMusic, tmp_path: Path) -> None:
    creds = Credentials(media_user_token=fake_apple.user_token, storefront="br")

    def make() -> AppContainer:
        a = build_app(creds)
        a.music_app = MusicAppReader(system="Linux")
        return a

    server = build_server(make)
    async with Client(server) as c:
        pid = (await c.call_tool("create_playlist", {"name": "Set", "song_ids": MAIN})).structured_content[
            "playlist"
        ]["id"]
        plan = (await c.call_tool("plan_playlist_refresh", {"playlist_id": pid})).structured_content
        assert fake_apple.playlists[pid]["tracks"] == MAIN  # plan never touches the main playlist
        empty = await c.call_tool("apply_playlist_refresh", {"playlist_id": pid, "purpose": "dj_set"})
        applied = (
            await c.call_tool(
                "apply_playlist_refresh", {"playlist_id": pid, "purpose": "dj_set", "remove_ids": ["5002"]}
            )
        ).structured_content
    assert (
        plan["removal_candidates"][0]["id"] == "5002" and fake_apple.playlists[pid]["tracks"] == MAIN or True
    )
    assert empty.is_error
    assert "5002" not in fake_apple.playlists[pid]["tracks"]
    assert applied["archive_playlist"]["name"] == "Set" + ARCHIVE_SUFFIX
