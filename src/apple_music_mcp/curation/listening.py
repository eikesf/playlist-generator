"""Plays and skips over a window of days, from the sparse daily snapshots (data model decision 3).

The Music app keeps cumulative counters per track, like an electricity meter. ``play_snapshot`` reads
the meter every night and stores a row only when a track's counters changed, plus one full baseline
on the first run. What was played between two days is the difference between the readings:

* the reading at the window start is the last row on or before that day;
* a track with no row before the window was added to the library later, so it starts from zero;
* a window that starts before the first snapshot is cut to the first snapshot (we cannot know more);
* duplicates of one song (album and single) are summed under their match key;
* a counter that went down (library reset) never gives negative plays.

With only the baseline there is no history at all (``history_days == 0``): callers must say so
instead of guessing (the refresh proposes no rotation until enough days were observed).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class SnapshotRow:
    track_id: int
    key: str
    day: date
    played: int
    skipped: int
    favorited: bool
    disliked: bool


@dataclass(frozen=True)
class Delta:
    plays: int
    skips: int
    favorited: bool
    disliked: bool
    first_seen: date

    @property
    def skip_ratio(self) -> float | None:
        total = self.plays + self.skips
        return self.skips / total if total else None


@dataclass
class ListeningWindow:
    start: date
    end: date
    history_start: date | None
    history_days: int
    deltas: dict[str, Delta] = field(default_factory=dict)


def _reading(rows: list[SnapshotRow], day: date) -> SnapshotRow | None:
    """The last reading on or before ``day`` (rows sorted by day)."""
    found = None
    for r in rows:
        if r.day > day:
            break
        found = r
    return found


def window_deltas(rows: Iterable[SnapshotRow], start: date, end: date) -> ListeningWindow:
    by_track: dict[int, list[SnapshotRow]] = {}
    for r in rows:
        by_track.setdefault(r.track_id, []).append(r)
    if not by_track:
        return ListeningWindow(start, end, None, 0)
    history_start = min(r.day for rs in by_track.values() for r in rs)
    effective = max(start, history_start)
    deltas: dict[str, Delta] = {}
    for track_rows in by_track.values():
        track_rows.sort(key=lambda r: r.day)
        last = _reading(track_rows, end)
        if last is None:  # first seen after the window
            continue
        base = _reading(track_rows, effective)
        plays = max(0, last.played - (base.played if base else 0))
        skips = max(0, last.skipped - (base.skipped if base else 0))
        current = Delta(plays, skips, last.favorited, last.disliked, track_rows[0].day)
        prev = deltas.get(last.key)
        deltas[last.key] = (
            current
            if prev is None
            else Delta(
                prev.plays + current.plays,
                prev.skips + current.skips,
                prev.favorited or current.favorited,
                prev.disliked or current.disliked,
                min(prev.first_seen, current.first_seen),
            )
        )
    return ListeningWindow(effective, end, history_start, max(0, (end - history_start).days), deltas)
