"""The weekly `refresh_plan` job (guide §7.10): deterministic proposals, ntfy, expiry, launchd."""

from __future__ import annotations

import plistlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from apple_music_mcp.container import AppContainer
from apple_music_mcp.db.history import ManagedPlaylistRepository, ProposalRepository
from apple_music_mcp.jobs import run_job
from apple_music_mcp.jobs.refresh_plan import due_playlists, last_slot
from apple_music_mcp.local_music import MusicAppReader
from apple_music_mcp.pause import pause
from apple_music_mcp.settings import AppSettings

from .fake_apple import FakeAppleMusic
from .test_refresh import MAIN, managed_main, week_of_listening

SP = ZoneInfo("America/Sao_Paulo")


class Recorder:
    def __init__(self, delivered: bool = True) -> None:
        self.sent: list[tuple[str, str]] = []
        self.delivered = delivered

    async def send(
        self, title: str, message: str, *, priority: str = "default", tags: tuple[str, ...] = ()
    ) -> bool:
        self.sent.append((title, message))
        return self.delivered


def opt_in(app: AppContainer, playlist_id: str, **fields: Any) -> None:
    ManagedPlaylistRepository(app.db).upsert(playlist_id, origin="adopted", refresh_enabled=True, **fields)


def test_last_slot_is_the_latest_scheduled_time_not_after_now() -> None:
    wednesday = datetime(2026, 11, 4, 15, 0, tzinfo=SP)
    assert last_slot(wednesday, "monday", "09:00") == datetime(2026, 11, 2, 9, 0, tzinfo=SP)
    monday_early = datetime(2026, 11, 2, 8, 59, tzinfo=SP)
    assert last_slot(monday_early, "monday", "09:00") == datetime(2026, 10, 26, 9, 0, tzinfo=SP)
    assert last_slot(datetime(2026, 11, 2, 9, 0, tzinfo=SP), "monday", "09:00").day == 2


def test_due_playlists_respect_opt_in_schedule_and_last_run() -> None:
    settings = AppSettings()
    now = datetime(2026, 11, 4, 12, 0, tzinfo=SP)  # Wednesday
    rows = [
        {"id": 1, "refresh_enabled": True, "status": "active", "refresh_schedule": None},
        {"id": 2, "refresh_enabled": False, "status": "active", "refresh_schedule": None},
        {"id": 3, "refresh_enabled": True, "status": "active", "refresh_schedule": {"weekday": "friday"}},
        {"id": 4, "refresh_enabled": True, "status": "retired", "refresh_schedule": None},
        {"id": 5, "refresh_enabled": True, "status": "active", "refresh_schedule": None},
    ]
    # 5 already got a proposal after Monday 09:00 this week; 1 only last week.
    last = {1: datetime(2026, 10, 27, 9, 0, tzinfo=UTC), 5: datetime(2026, 11, 2, 12, 5, tzinfo=UTC)}
    due = [r["id"] for r in due_playlists(rows, settings, now, last)]
    assert due == [1, 3]  # 3: last Friday's slot, never run


async def test_job_creates_and_notifies_one_proposal_per_opted_in_playlist(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    app.music_app = MusicAppReader(system="Linux")
    main = await managed_main(app)
    other = await managed_main(app, "Not opted in")
    week_of_listening(fake_apple, app)
    opt_in(app, main.id)
    notifier = Recorder()
    before = [c for c in fake_apple.calls if c[0] != "GET"]

    outcome = await run_job("refresh_plan", app, notifier)

    assert [c for c in fake_apple.calls if c[0] != "GET"] == before  # the job never writes to Apple
    assert fake_apple.playlists[main.id]["tracks"] == MAIN
    [created] = outcome.detail["proposals"]
    assert created["playlist"] == "Warehouse" and created["remove"] == 3
    repo = ProposalRepository(app.db)
    [p] = repo.find(kind="refresh")
    assert p["job_run_id"] is not None and p["notified_at"] is not None
    assert p["managed_playlist_id"] != ManagedPlaylistRepository(app.db).get(other.id)["id"]  # type: ignore[index]
    title, body = notifier.sent[0]
    assert title == f"Refresh pronto para Warehouse: 3 saem, {created['add']} entram"
    assert "Claude" in body

    # Same day again: idempotent, nothing new, no second alert.
    again = await run_job("refresh_plan", app, notifier)
    assert again.detail["proposals"] == [] and len(notifier.sent) == 1
    assert len(repo.find(kind="refresh")) == 1


async def test_failed_delivery_leaves_notified_at_empty(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    app.music_app = MusicAppReader(system="Linux")
    main = await managed_main(app)
    opt_in(app, main.id)
    await run_job("refresh_plan", app, Recorder(delivered=False))
    [p] = ProposalRepository(app.db).find(kind="refresh")
    assert p["notified_at"] is None


async def test_old_pending_proposals_expire_and_a_fresh_one_is_made(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    app.music_app = MusicAppReader(system="Linux")
    main = await managed_main(app)
    opt_in(app, main.id)
    repo = ProposalRepository(app.db)
    managed = ManagedPlaylistRepository(app.db).get(main.id)
    assert managed is not None
    stale = repo.create(
        "refresh",
        [],
        managed_playlist_id=managed["id"],
        params={"playlist_id": main.id},
        created_at=datetime.now(UTC) - timedelta(days=9),
    )
    outcome = await run_job("refresh_plan", app, Recorder())
    assert repo.get(stale)["status"] == "expired"  # type: ignore[index]
    assert outcome.detail["expired"] == [stale]
    assert len(outcome.detail["proposals"]) == 1


async def test_pause_mode_skips_the_job(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    main = await managed_main(app)
    opt_in(app, main.id)
    pause(app.state, None, "trip")
    outcome = await run_job("refresh_plan", app, Recorder())
    assert outcome.status == "skipped_paused"
    assert ProposalRepository(app.db).find(kind="refresh") == []


async def test_auto_apply_off_never_applies_and_on_applies(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    app.music_app = MusicAppReader(system="Linux")
    main = await managed_main(app)
    opt_in(app, main.id)
    await run_job("refresh_plan", app, Recorder())
    [p] = ProposalRepository(app.db).find(kind="refresh")
    assert p["status"] == "pending_approval" and "5002" in fake_apple.playlists[main.id]["tracks"]

    other = await managed_main(app, "Auto")
    opt_in(app, other.id)
    app.settings = replace(app.settings, refresh_auto_apply=True)
    outcome = await run_job("refresh_plan", app, Recorder())
    [auto] = outcome.detail["proposals"]
    assert auto["auto_applied"] == "applied"
    assert "5002" not in fake_apple.playlists[other.id]["tracks"]  # the dislike went (archived first)


async def test_playlist_without_anything_to_change_is_not_notified(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    app.music_app = MusicAppReader(system="Linux")
    pl = await app.library.create_playlist("Calm", "x")
    await app.library.add_tracks(pl.id, ["4001", "1001"])
    from apple_music_mcp.sessions import CurationSessions

    CurationSessions(app).adopt(pl.id, "Calm", "chill")
    opt_in(app, pl.id)
    notifier = Recorder()
    outcome = await run_job("refresh_plan", app, notifier)
    assert notifier.sent == [] and outcome.detail["proposals"][0]["remove"] == 0
    [p] = ProposalRepository(app.db).find(statuses=("applied",), kind="refresh")
    assert p["params"]["notes"]


def test_launchd_refresh_agent_has_one_slot_per_schedule(tmp_path: Path) -> None:
    from apple_music_mcp.commands.jobs import LABEL_PREFIX, write_agents

    paths = write_agents(
        AppSettings(),
        executable="/x/apple-music-mcp",
        directory=tmp_path,
        logs=tmp_path / "logs",
        refresh_slots=[("monday", "09:00"), ("friday", "18:30"), ("monday", "09:00")],
    )
    plists = {p.name: plistlib.loads(p.read_bytes()) for p in paths}
    agent = plists[f"{LABEL_PREFIX}refresh_plan.plist"]
    assert agent["ProgramArguments"] == ["/x/apple-music-mcp", "run", "refresh_plan"]
    assert agent["StartCalendarInterval"] == [
        {"Weekday": 1, "Hour": 9, "Minute": 0},
        {"Weekday": 5, "Hour": 18, "Minute": 30},
    ]
    assert len(plists) == 6


def test_refresh_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFRESH_WEEKDAY", "Tuesday")
    monkeypatch.setenv("REFRESH_TIME", "7:05")
    monkeypatch.setenv("REFRESH_SKIP_RATIO", "2")  # out of range: default kept
    s = AppSettings.from_env()
    assert (s.refresh_weekday, s.refresh_time, s.refresh_skip_ratio) == ("tuesday", "07:05", 0.5)
    monkeypatch.setenv("REFRESH_WEEKDAY", "someday")
    assert AppSettings.from_env().refresh_weekday == "monday"


async def test_update_managed_playlist_sets_refresh_opt_in_and_slot(fake_apple: FakeAppleMusic) -> None:
    from mcp import Client

    from apple_music_mcp.config import Credentials
    from apple_music_mcp.server import build_server

    from .conftest import build_app

    creds = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    async with Client(build_server(lambda: build_app(creds))) as c:
        pid = (await c.call_tool("create_playlist", {"name": "Set", "song_ids": MAIN})).structured_content[
            "playlist"
        ]["id"]
        await c.call_tool("adopt_playlist", {"playlist_id": pid, "name": "Set", "purpose": "dj_set"})
        out = (
            await c.call_tool(
                "update_managed_playlist",
                {
                    "playlist_id": pid,
                    "refresh_enabled": True,
                    "refresh_weekday": "friday",
                    "refresh_time": "18:5",
                },
            )
        ).structured_content
        bad = await c.call_tool("update_managed_playlist", {"playlist_id": pid, "refresh_time": "25:00"})
    managed = out["managed_playlist"]
    assert managed["refresh_enabled"] is True
    assert managed["refresh_schedule"] == {"weekday": "friday", "time": "18:05"}
    assert "schedule install" in out["note"] and bad.is_error
