"""Play/skip windows from sparse daily snapshots (data model decision 3): the refresh's evidence."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from apple_music_mcp.curation.listening import SnapshotRow, window_deltas
from apple_music_mcp.db import Database
from apple_music_mcp.db.repositories import (
    Counters,
    JobRunRepository,
    ListeningRepository,
    SnapshotRepository,
    TrackInfo,
    TrackRepository,
)

D = date(2026, 9, 1)


def row(track: int, day: int, played: int, skipped: int = 0, fav: bool = False, key: str = "") -> SnapshotRow:
    return SnapshotRow(
        track_id=track,
        key=key or f"k{track}",
        day=date(2026, 9, day),
        played=played,
        skipped=skipped,
        favorited=fav,
        disliked=False,
    )


def test_first_snapshot_only_has_no_history() -> None:
    w = window_deltas([row(1, 1, 40), row(2, 1, 3)], start=date(2026, 7, 20), end=date(2026, 9, 1))
    assert w.history_start == date(2026, 9, 1) and w.history_days == 0
    assert w.deltas["k1"].plays == 0 and w.deltas["k2"].plays == 0  # the baseline is not "plays"


def test_no_rows_means_no_history() -> None:
    w = window_deltas([], start=date(2026, 7, 20), end=D)
    assert w.history_start is None and w.history_days == 0 and w.deltas == {}


def test_gaps_use_the_last_counter_before_the_window() -> None:
    # Sparse: a row only on days the counters changed.
    rows = [row(1, 1, 10, 1), row(1, 5, 12, 1), row(1, 20, 15, 4), row(1, 28, 16, 4)]
    w = window_deltas(rows, start=date(2026, 9, 10), end=date(2026, 9, 25))
    d = w.deltas["k1"]
    assert (d.plays, d.skips) == (3, 3)  # 12/1 on day 5 is the baseline, 15/4 on day 20 the end
    assert d.skip_ratio == 0.5
    assert w.history_days == 24


def test_window_before_the_history_starts_is_cut_to_the_history() -> None:
    rows = [row(1, 1, 10), row(1, 8, 13)]
    w = window_deltas(rows, start=date(2026, 7, 1), end=date(2026, 9, 10))
    assert w.start == date(2026, 9, 1) and w.deltas["k1"].plays == 3


def test_a_track_added_after_the_baseline_counts_from_zero() -> None:
    rows = [row(1, 1, 10), row(2, 6, 2, 1)]
    w = window_deltas(rows, start=date(2026, 9, 1), end=date(2026, 9, 10))
    assert (w.deltas["k2"].plays, w.deltas["k2"].skips) == (2, 1)
    assert w.deltas["k2"].first_seen == date(2026, 9, 6)
    assert w.deltas["k1"].first_seen == date(2026, 9, 1)


def test_duplicates_of_one_song_add_up_and_keep_the_latest_flags() -> None:
    rows = [
        row(1, 1, 5, key="same"),
        row(2, 1, 1, key="same"),
        row(1, 9, 7, key="same"),
        row(2, 9, 4, 2, fav=True, key="same"),
    ]
    d = window_deltas(rows, start=date(2026, 9, 1), end=date(2026, 9, 10)).deltas["same"]
    assert (d.plays, d.skips, d.favorited) == (5, 2, True)


def test_counter_reset_never_gives_negative_plays() -> None:
    rows = [row(1, 1, 10), row(1, 5, 2)]
    assert window_deltas(rows, start=D, end=date(2026, 9, 9)).deltas["k1"].plays == 0


def test_skip_ratio_is_none_without_activity() -> None:
    d = window_deltas([row(1, 1, 3)], start=D, end=D).deltas["k1"]
    assert d.skip_ratio is None


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database.open()
    yield database
    database.dispose()


def _run(db: Database) -> int:
    with JobRunRepository(db).run("snapshot") as (run_id, _):
        return run_id


def test_repository_window_reads_the_snapshot_table(db: Database) -> None:
    tracks = TrackRepository(db)
    snaps = SnapshotRepository(db)
    first, second = _run(db), _run(db)
    with db.engine.begin() as conn:
        a = tracks.upsert(TrackInfo("Doppler", "Charlotte de Witte", music_app_pid="P1"), conn)
        snaps.record(conn, date(2026, 9, 1), first, {a: Counters(10, 0, False, False)})
        snaps.record(conn, date(2026, 9, 15), second, {a: Counters(14, 2, False, False)})
    w = ListeningRepository(db).window(date(2026, 9, 2), date(2026, 9, 20))
    [(key, d)] = w.deltas.items()
    assert key.endswith("|doppler") and (d.plays, d.skips) == (4, 2)
    assert w.history_days == 19
