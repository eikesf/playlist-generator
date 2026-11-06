"""Promotion from Weekly Discoveries (guide §7.11): liked tracks only, at most 20 % / 30 a week, approved."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import update

from apple_music_mcp.container import AppContainer
from apple_music_mcp.curation.listening import Delta
from apple_music_mcp.db.history import ManagedPlaylistRepository, PlaylistEventRepository, ProposalRepository
from apple_music_mcp.db.schema import proposal
from apple_music_mcp.jobs import run_job
from apple_music_mcp.local_music import MusicAppReader
from apple_music_mcp.promotion import PromotionService, is_liked, promotion_cap
from apple_music_mcp.proposals import ProposalService
from apple_music_mcp.weekly_discoveries import WeeklyDiscoveries

from .fake_apple import FakeAppleMusic
from .test_refresh import MAIN, seed
from .test_refresh_job import Recorder
from .test_weekly_discoveries import NEW_IDS, add_new_songs, enabled_main

TODAY = date.today()


def test_cap_is_20_percent_rounded_down_never_above_30_never_below_0() -> None:
    assert promotion_cap(10, 0) == 2
    assert promotion_cap(9, 0) == 1
    assert promotion_cap(4, 0) == 0
    assert promotion_cap(500, 0) == 30
    assert promotion_cap(100, 15) == 5  # 15 promoted earlier this week
    assert promotion_cap(100, 25) == 0
    assert promotion_cap(0, 0) == 0
    assert promotion_cap(200, 3, fraction=0.1, maximum=10) == 7


def d(plays: int, skips: int = 0, fav: bool = False) -> Delta:
    return Delta(plays, skips, fav, False, TODAY)


def test_liked_means_loved_or_played_with_few_skips() -> None:
    assert is_liked(None, True, 3, 0.25) == "loved"
    assert is_liked(d(0, fav=True), False, 3, 0.25) == "loved"
    assert is_liked(d(3), False, 3, 0.25) == "played 3x, skipped 0x"
    assert is_liked(d(6, 1), False, 3, 0.25) == "played 6x, skipped 1x"  # 1/7 < 0.25
    assert is_liked(d(3, 1), False, 3, 0.25) is None  # 1/4 is not below 0.25
    assert is_liked(d(2), False, 3, 0.25) is None
    assert is_liked(None, False, 3, 0.25) is None


async def last_weeks_batch(app: AppContainer, fake: FakeAppleMusic) -> tuple[str, str]:
    """A main playlist with a batch submitted 7 days ago, and the listening since then:
    77000 loved, 77003 played 5x, 77001 played 4x, 77002 played 3x but skipped 2x, 77004 played 2x."""
    add_new_songs(fake)
    pid = await enabled_main(app)
    batch = await WeeklyDiscoveries(app).submit(pid, NEW_IDS[:30])
    with app.db.engine.begin() as conn:
        conn.execute(
            update(proposal)
            .where(proposal.c.id == batch["proposal_id"])
            .values(created_at=datetime.now(UTC) - timedelta(days=7))
        )
    seed(app, fake, {30: {sid: (20, 0) for sid in MAIN}})
    seed(
        app,
        fake,
        {
            4: {"77001": (2, 0), "77003": (3, 0)},
            1: {"77001": (4, 0), "77002": (3, 2), "77003": (5, 0), "77004": (2, 0)},
        },
    )
    fake.ratings["77000"] = 1
    return pid, batch["weekly_playlist"]["id"]


async def test_build_proposes_only_liked_tracks_within_the_cap(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pid, weekly = await last_weeks_batch(app, fake_apple)
    managed = ManagedPlaylistRepository(app.db).get(pid)
    assert managed is not None
    before = [c for c in fake_apple.calls if c[0] != "GET"]
    out = await PromotionService(app).build(managed)
    assert [c for c in fake_apple.calls if c[0] != "GET"] == before  # read-only
    assert out["kind"] == "promotion" and out["status"] == "pending_approval"
    assert out["cap"] == 2 and out["liked"] == 3  # 20 % of 10 tracks
    ids = [i["song_id"] for i in out["items"]]
    assert ids == ["77000", "77003"]  # loved first, then most played; 77001 waits for next week
    assert out["items"][0]["why"] == "loved" and out["items"][1]["why"] == "played 5x, skipped 0x"
    again = await PromotionService(app).build(managed)
    assert again["proposal_id"] == out["proposal_id"] and again["already_open"] is True


async def test_apply_inserts_keeping_order_records_history_and_leaves_the_weekly_playlist(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pid, weekly = await last_weeks_batch(app, fake_apple)
    managed = ManagedPlaylistRepository(app.db).get(pid)
    assert managed is not None
    out = await PromotionService(app).build(managed)
    items = {i["song_id"]: i["item_id"] for i in out["items"]}
    service = ProposalService(app)
    service.decide(out["proposal_id"], accept=[items["77000"]], reject=[(items["77003"], "não combina")])
    result = await service.apply(out["proposal_id"])

    main = fake_apple.playlists[pid]["tracks"]
    assert "77000" in main and "77003" not in main
    assert [t for t in main if t != "77000"] == MAIN  # existing order kept
    weekly_now = fake_apple.playlists[weekly]["tracks"]
    assert "77000" not in weekly_now and "77003" in weekly_now
    assert result["status"] == "applied"
    events = PlaylistEventRepository(app.db).history(managed["id"])
    assert ("77000", "added", "promotion") in {(e["catalog_id"], e["event"], e["origin"]) for e in events}
    # The next build counts this week's promotion against the cap: 2 - 1 = 1 left.
    later = await PromotionService(app).build(managed)
    assert later["cap"] == 1 and [i["song_id"] for i in later["items"]] == ["77001"]


async def test_liked_tracks_waiting_for_approval_survive_the_next_batch(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    pid, weekly = await last_weeks_batch(app, fake_apple)
    managed = ManagedPlaylistRepository(app.db).get(pid)
    assert managed is not None
    await PromotionService(app).build(managed)
    nxt = await WeeklyDiscoveries(app).submit(pid, NEW_IDS[6:36])
    tracks = fake_apple.playlists[weekly]["tracks"]
    assert tracks[:2] == ["77000", "77003"] and nxt["held_for_promotion"] == ["77000", "77003"]
    assert set(NEW_IDS[6:36]) <= set(tracks)


async def test_job_builds_promotions_for_discoveries_playlists_and_notifies(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    app.music_app = MusicAppReader(system="Linux")
    pid, _ = await last_weeks_batch(app, fake_apple)
    notifier = Recorder()
    outcome = await run_job("refresh_plan", app, notifier)
    [promo] = outcome.detail["promotions"]
    assert promo["playlist"] == "Warehouse" and promo["add"] == 2
    assert ("Promoção pronta para Warehouse: 2 faixas das Weekly Discoveries") in [
        t for t, _ in notifier.sent
    ]
    [p] = ProposalRepository(app.db).find(kind="promotion")
    assert p["notified_at"] is not None and p["job_run_id"] is not None
    again = await run_job("refresh_plan", app, notifier)
    assert again.detail["promotions"] == []


async def test_nothing_liked_means_no_proposal(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    add_new_songs(fake_apple)
    pid = await enabled_main(app)
    await WeeklyDiscoveries(app).submit(pid, NEW_IDS[:30])
    managed = ManagedPlaylistRepository(app.db).get(pid)
    assert managed is not None
    out = await PromotionService(app).build(managed)
    assert out["proposal_id"] is None and out["liked"] == 0
    assert ProposalRepository(app.db).find(kind="promotion") == []
