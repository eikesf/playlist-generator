"""Scheduled jobs: snapshots, recently played, doctor, pause mode, launchd files."""

from __future__ import annotations

import plistlib
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import func, select

from apple_music_mcp.config import Credentials
from apple_music_mcp.db.repositories import JobRunRepository, count_new_items
from apple_music_mcp.db.schema import job_run, play_snapshot, recent_play, track
from apple_music_mcp.jobs import NullNotifier, run_job
from apple_music_mcp.local_music import SEP, MusicAppReader, parse_library
from apple_music_mcp.pause import pause, pause_status, resume
from apple_music_mcp.settings import AppSettings

from .conftest import build_app
from .fake_apple import FakeAppleMusic


def library_line(
    pid: str, name: str, artist: str, plays: int, skips: int = 0, fav: str = "false", duration: str = "245,5"
) -> str:
    return SEP.join(
        [pid, name, artist, "missing value", "Techno", duration, str(plays), str(skips), fav, "false"]
    )


def music_app(lines: list[list[str]]) -> MusicAppReader:
    async def runner(args: list[str]) -> tuple[int, str, str]:
        if args[0] == "library":
            return 0, "\n".join(lines[0]), ""
        return 1, "", "unexpected"

    return MusicAppReader(runner=runner, system="Darwin")


def test_parse_library_handles_locale_decimal_and_missing_values() -> None:
    [t] = parse_library(library_line("ABC123", "Doppler", "Charlotte de Witte", 12, 1, "true"))
    assert (t.persistent_id, t.album, t.genre, t.duration_ms) == ("ABC123", None, "Techno", 245500)
    assert (t.played, t.skipped, t.favorited) == (12, 1, True)


async def test_snapshot_is_sparse_and_idempotent(logged_in_creds: Credentials) -> None:
    lines = [
        [
            library_line("P1", "Doppler", "Charlotte de Witte", 12),
            library_line("P2", "Feel It", "Amelie Lens", 3),
        ]
    ]
    async with build_app(logged_in_creds, music_app=music_app(lines)) as app:
        first = await run_job("snapshot", app)
        assert first.detail["rows_written"] == 2  # baseline
        again = await run_job("snapshot", app)
        assert again.detail["rows_written"] == 0 and again.detail["unchanged"] == 2
        lines[0] = [library_line("P1", "Doppler", "Charlotte de Witte", 13), lines[0][1]]
        changed = await run_job("snapshot", app)
        assert changed.detail["rows_written"] == 1
        with app.db.engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(track)).scalar_one() == 2
            # same calendar day: the changed row replaced that day's value
            plays = conn.execute(
                select(play_snapshot.c.played_count).order_by(play_snapshot.c.played_count)
            ).scalars()
            assert list(plays) == [3, 13]


def test_new_items_are_counted_from_the_previous_newest_item() -> None:
    assert count_new_items([], [1, 2, 3]) == (3, False)
    assert count_new_items([1, 2, 3], [9, 8, 1, 2, 3]) == (2, False)
    assert count_new_items([1, 2, 3], [1, 2, 3]) == (0, False)
    assert count_new_items([1, 2, 3], [7, 8, 9]) == (3, True)  # history overflowed between runs


async def test_recent_played_records_each_run_and_counts_new_plays(
    fake_apple: FakeAppleMusic, logged_in_creds: Credentials
) -> None:
    fake_apple.recent_ids = ["1002", "1001"]
    async with build_app(logged_in_creds) as app:
        first = await run_job("recent_played", app)
        assert first.detail["items_returned"] == 2 and first.detail["new_items"] == 2
        fake_apple.recent_ids = ["2001", "1002", "1001"]
        second = await run_job("recent_played", app)
        assert second.detail["new_items"] == 1 and second.status == "ok"
        with app.db.engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(recent_play)).scalar_one() == 5


async def test_doctor_job_records_checks_and_alerts_on_failure(
    fake_apple: FakeAppleMusic, logged_in_creds: Credentials
) -> None:
    notifier = NullNotifier()
    async with build_app(logged_in_creds, music_app=MusicAppReader(system="Linux")) as app:
        outcome = await run_job("doctor", app, notifier)
        assert outcome.status == "warn"  # AppleScript is unavailable off macOS: a warning, not a failure
        names = {c["name"] for c in outcome.detail["checks"]}
        assert "create + reorder playlist (write test)" not in names  # the daily job never writes
        assert notifier.sent == []
        fake_apple.amp_available = False
        failed = await run_job("doctor", app, notifier)
        assert failed.status == "failed" and notifier.sent and notifier.sent[0][2] == "high"
        last = JobRunRepository(app.db).last("doctor")
        assert last is not None and last["status"] == "failed"


async def test_failing_job_is_recorded_alerted_and_raised(logged_in_creds: Credentials) -> None:
    notifier = NullNotifier()

    async def broken(args: list[str]) -> tuple[int, str, str]:
        return 1, "", "execution error: Not authorized to send Apple events to Music. (-1743)"

    async with build_app(logged_in_creds, music_app=MusicAppReader(runner=broken, system="Darwin")) as app:
        with pytest.raises(Exception, match="Automation"):
            await run_job("snapshot", app, notifier)
        last = JobRunRepository(app.db).last("snapshot")
        assert last and last["status"] == "failed" and "Automation" in last["error"]
        assert notifier.sent[0][0] == "apple-music-mcp: snapshot failed"


async def test_pause_mode_skips_jobs_until_resumed(logged_in_creds: Credentials) -> None:
    async with build_app(logged_in_creds) as app:
        pause(app.state, None, "trip")
        assert pause_status(app.state)
        outcome = await run_job("snapshot", app)
        assert outcome.status == "skipped_paused"
        resume(app.state)
        assert pause_status(app.state) is None
        with app.db.engine.connect() as conn:
            assert conn.execute(select(job_run.c.status)).scalars().all() == ["skipped_paused"]


def test_launchd_agents_follow_the_settings(tmp_path: Path) -> None:
    from apple_music_mcp.commands.jobs import LABEL_PREFIX, write_agents

    settings = replace(AppSettings(), recent_played_times=("10:30", "12:30", "22:30"), heartbeat_minutes=10)
    paths = write_agents(
        settings, executable="/x/apple-music-mcp", directory=tmp_path, logs=tmp_path / "logs"
    )
    plists = {p.name: plistlib.loads(p.read_bytes()) for p in paths}
    recent = plists[f"{LABEL_PREFIX}recent_played.plist"]
    assert recent["ProgramArguments"] == ["/x/apple-music-mcp", "run", "recent_played"]
    assert recent["StartCalendarInterval"] == [
        {"Hour": 10, "Minute": 30},
        {"Hour": 12, "Minute": 30},
        {"Hour": 22, "Minute": 30},
    ]
    assert plists[f"{LABEL_PREFIX}heartbeat.plist"]["StartInterval"] == 600
    assert len(plists) == 6  # + refresh_plan
