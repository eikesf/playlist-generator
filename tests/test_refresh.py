"""Weekly refresh: staging for discoveries, archive-before-remove, never lose a track silently."""

from __future__ import annotations

from collections.abc import Collection
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from apple_music_mcp.config import Credentials
from apple_music_mcp.container import AppContainer
from apple_music_mcp.db.history import PlaylistEventRepository, ProposalRepository
from apple_music_mcp.db.repositories import (
    Counters,
    JobRunRepository,
    SnapshotRepository,
    TrackInfo,
    TrackRepository,
)
from apple_music_mcp.local_music import (
    SEP,
    MusicAppReader,
    MusicAppUnavailable,
    PlaylistNotInMusicApp,
    parse_stats,
)
from apple_music_mcp.models import Playlist
from apple_music_mcp.proposals import ProposalService
from apple_music_mcp.refresh import ARCHIVE_SUFFIX, RefreshService
from apple_music_mcp.server import build_server
from apple_music_mcp.sessions import CurationSessions

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


TODAY = date.today()


def seed(app: AppContainer, fake: FakeAppleMusic, readings: dict[int, dict[str, tuple[Any, ...]]]) -> None:
    """Write Music app snapshots: ``{days_ago: {song_id: (played, skipped[, favorited])}}``, oldest first."""
    tracks, snaps, runs = TrackRepository(app.db), SnapshotRepository(app.db), JobRunRepository(app.db)
    for days_ago in sorted(readings, reverse=True):
        with runs.run("snapshot") as (run_id, _):
            pass
        with app.db.engine.begin() as conn:
            counters = {}
            for sid, values in readings[days_ago].items():
                song = fake.songs_by_id[sid]
                tid = tracks.upsert(TrackInfo(song.name, song.artist, music_app_pid=f"P{sid}"), conn)
                fav = bool(values[2]) if len(values) > 2 else False
                counters[tid] = Counters(values[0], values[1], fav, False)
            snaps.record(conn, TODAY - timedelta(days=days_ago), run_id, counters)


def week_of_listening(fake: FakeAppleMusic, app: AppContainer) -> None:
    """60 days of history: 4002 never played lately, 2003 mostly skipped, 3001 a favorite,
    8001 added 3 days ago, the rest played normally. 5002 is rated "dislike" in the fake."""
    base = {sid: (20, 0) for sid in MAIN if sid != "8001"}
    later = {
        "4001": (30, 0),
        "2003": (21, 6),
        "3001": (20, 0, True),
        "1001": (28, 0),
        "2002": (25, 1),
        "5001": (26, 0),
        "5002": (22, 0),
        "7001": (24, 0),
    }
    seed(app, fake, {60: base, 10: later, 3: {"8001": (0, 0)}})


def apple_writes(fake: FakeAppleMusic, since: int) -> list[tuple[str, str]]:
    return [c for c in fake.calls[since:] if c[0] != "GET"]


async def managed_main(app: AppContainer, name: str = "Warehouse") -> Playlist:
    pl = await _main_playlist(app, name)
    CurationSessions(app).adopt(pl.id, name, "dj_set")
    return pl


def items_by(plan: dict[str, Any], action: str) -> list[dict[str, Any]]:
    return [i for i in plan["items"] if i["action"] == action]


async def test_plan_is_read_only_and_proposes_skipped_and_unplayed_tracks(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await managed_main(app)
    week_of_listening(fake_apple, app)
    app.blacklist_entries.add_artist("Anyma", "nunca mais Anyma")
    before = len(fake_apple.calls)
    plan = await RefreshService(app, MusicAppReader(system="Linux")).build_plan(pl.id)

    assert apple_writes(fake_apple, before) == []  # nothing created, nothing edited
    assert fake_apple.playlists[pl.id]["tracks"] == MAIN and len(fake_apple.playlists) == 1
    assert plan["kind"] == "refresh" and plan["status"] == "pending_approval"
    removes = {i["song_id"]: i for i in items_by(plan, "remove")}
    assert set(removes) == {"2003", "4002", "5002"}
    assert removes["2003"]["why"] == "often_skipped" and removes["2003"]["skips"] == 6
    assert removes["4002"]["why"] == "not_played" and removes["4002"]["plays"] == 0
    assert removes["5002"]["why"] == "disliked"
    # Never: the favorite (3001), the track added 3 days ago (8001), and nothing is decided yet.
    assert all(i["decision"] == "pending" for i in plan["items"])
    adds = items_by(plan, "add")
    assert adds, "replacements are proposed"
    remove_item_ids = {i["item_id"] for i in removes.values()}
    for add in adds:
        assert add["replaces_item_id"] in remove_item_ids
        assert add["song_id"] not in MAIN and add["song_id"] not in {"9001", "9002"}  # blacklisted
    assert plan["summary"]["remove"] == 3


async def test_too_little_history_proposes_no_rotation_and_says_why(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await managed_main(app)
    seed(app, fake_apple, {5: {sid: (20, 0) for sid in MAIN}, 1: {"4001": (25, 0)}})
    plan = await RefreshService(app, MusicAppReader(system="Linux")).build_plan(pl.id)
    assert {i["song_id"] for i in items_by(plan, "remove")} == {"5002"}  # only the dislike
    assert any("5 day(s) of play history" in n for n in plan["notes"])


async def test_unplayed_playlist_gives_nothing_to_learn(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await managed_main(app)
    seed(app, fake_apple, {40: {sid: (20, 0) for sid in MAIN}})
    plan = await RefreshService(app, MusicAppReader(system="Linux")).build_plan(pl.id)
    assert {i["song_id"] for i in items_by(plan, "remove")} == {"5002"}
    assert any("was not played" in n for n in plan["notes"])


async def test_no_play_data_at_all(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    pl = await managed_main(app)
    plan = await RefreshService(app, MusicAppReader(system="Linux")).build_plan(pl.id)
    assert {i["song_id"] for i in items_by(plan, "remove")} == {"5002"}
    assert any("0 day(s) of play history" in n for n in plan["notes"])


async def test_recently_added_by_the_music_app_is_protected(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await managed_main(app)
    week_of_listening(fake_apple, app)
    reader = fake_music_app(fake_apple, {}, recent={"4002"})
    plan = await RefreshService(app, reader).build_plan(pl.id)
    assert "4002" not in {i["song_id"] for i in items_by(plan, "remove")}


async def test_only_one_open_refresh_per_playlist(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    pl = await managed_main(app)
    service = RefreshService(app, MusicAppReader(system="Linux"))
    first = await service.build_plan(pl.id)
    again = await service.build_plan(pl.id)
    assert again["proposal_id"] == first["proposal_id"] and again["already_open"] is True
    assert len(ProposalRepository(app.db).find(kind="refresh")) == 1


async def test_lifecycle_partial_approval_applies_only_what_was_accepted(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await managed_main(app)
    week_of_listening(fake_apple, app)
    plan = await RefreshService(app, MusicAppReader(system="Linux")).build_plan(pl.id)
    removes = {i["song_id"]: i for i in items_by(plan, "remove")}
    replacement = next(
        a for a in items_by(plan, "add") if a["replaces_item_id"] == removes["4002"]["item_id"]
    )
    service = ProposalService(app)

    with pytest.raises(ValueError, match="approved"):
        await service.apply(plan["proposal_id"])  # still pending: nothing happens
    assert fake_apple.playlists[pl.id]["tracks"] == MAIN

    accept = [removes["4002"]["item_id"], replacement["item_id"]]
    others = [i["item_id"] for i in plan["items"] if i["item_id"] not in accept]
    decided = service.decide(
        plan["proposal_id"],
        accept=accept,
        reject=[(i, "quero manter") for i in others],
    )
    assert decided["status"] == "approved"
    result = await service.apply(plan["proposal_id"])

    main = fake_apple.playlists[pl.id]["tracks"]
    assert "4002" not in main and replacement["song_id"] in main
    assert {"2003", "5002"} <= set(main)  # rejected removals stay
    assert [t for t in main if t != replacement["song_id"]] == [t for t in MAIN if t != "4002"]
    archive = result["archive_playlist"]
    assert archive["name"] == "Warehouse" + ARCHIVE_SUFFIX
    assert fake_apple.playlists[archive["id"]]["tracks"] == ["4002"]
    assert ProposalRepository(app.db).get(plan["proposal_id"])["status"] == "applied"  # type: ignore[index]

    managed = CurationSessions(app).managed.get(pl.id)
    assert managed and managed["archive_apple_playlist_id"] == archive["id"]
    events = PlaylistEventRepository(app.db).history(managed["id"])
    got = {(e["catalog_id"], e["event"], e["origin"]) for e in events}
    assert {("4002", "archived", "refresh"), ("4002", "removed", "refresh")} <= got
    assert (replacement["song_id"], "added", "refresh") in got
    # A rejected removal is not a track to avoid; rejected additions are.
    avoid = {t["catalog_id"] for t in CurationSessions(app).start("edit", "x")["avoid"]}
    assert not avoid & {"2003", "5002"}


async def test_rejecting_everything_rejects_the_proposal(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await managed_main(app)
    plan = await RefreshService(app, MusicAppReader(system="Linux")).build_plan(pl.id)
    out = ProposalService(app).decide(plan["proposal_id"], reject_all=True, reason="tudo certo assim")
    assert out["status"] == "rejected" and all(i["reason"] == "tudo certo assim" for i in out["items"])
    with pytest.raises(ValueError):
        await ProposalService(app).apply(plan["proposal_id"])
    assert fake_apple.playlists[pl.id]["tracks"] == MAIN


async def test_refused_removal_keeps_main_and_archive_copy(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    fake_apple.remove_variants = set()
    pl = await managed_main(app)
    plan = await RefreshService(app, MusicAppReader(system="Linux")).build_plan(pl.id)
    service = ProposalService(app)
    service.decide(plan["proposal_id"], accept_all=True)
    result = await service.apply(plan["proposal_id"])
    assert result["status"] == "needs_decision"
    assert fake_apple.playlists[pl.id]["tracks"] == MAIN
    assert fake_apple.playlists[result["archive_playlist"]["id"]]["tracks"] == ["5002"]
    assert ProposalRepository(app.db).get(plan["proposal_id"])["status"] == "approved"  # type: ignore[index]


async def test_manual_apply_archives_before_removing_and_keeps_order(
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


async def test_archive_that_cannot_be_verified_removes_nothing(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pl = await _main_playlist(app)
    service = RefreshService(app, MusicAppReader(system="Linux"))
    original = app.library.add_tracks

    async def lossy_add(playlist_id: str, ids: list[str], kind: str = "songs") -> int:
        return 0 if playlist_id != pl.id else await original(playlist_id, ids, kind)

    app.library.add_tracks = lossy_add  # type: ignore[method-assign, assignment]
    with pytest.raises(Exception, match="archive"):
        await service.apply(pl.id, "dj_set", remove_ids=["5002"])
    assert fake_apple.playlists[pl.id]["tracks"] == MAIN


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
    assert plan["kind"] == "refresh" and {i["song_id"] for i in plan["items"]} >= {"5002"}
    assert empty.is_error
    assert "5002" not in fake_apple.playlists[pid]["tracks"]
    assert applied["archive_playlist"]["name"] == "Set" + ARCHIVE_SUFFIX
