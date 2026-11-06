"""Daily snapshot of the Music app's play/skip counters (guide §8.2, docs/data-model.md decision 3)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..container import AppContainer
from ..db.repositories import Counters, JobOutcome, SnapshotRepository, TrackInfo, TrackRepository
from .core import Notifier


async def snapshot_job(app: AppContainer, run_id: int, outcome: JobOutcome, notifier: Notifier) -> None:
    library = await app.music_app.library_stats()  # local AppleScript, no network
    tracks = TrackRepository(app.db)
    snapshots = SnapshotRepository(app.db)
    day = datetime.now(ZoneInfo(app.settings.timezone)).date()
    counters: dict[int, Counters] = {}
    with app.db.engine.begin() as conn:
        for item in library:
            if not item.name:
                continue
            track_id = tracks.upsert(
                TrackInfo(
                    title=item.name,
                    artist_name=item.artist,
                    music_app_pid=item.persistent_id,
                    album=item.album,
                    genre=item.genre,
                    duration_ms=item.duration_ms,
                ),
                conn,
            )
            counters[track_id] = Counters(item.played, item.skipped, item.favorited, item.disliked)
        written, unchanged = snapshots.record(conn, day, run_id, counters)
    outcome.detail = {
        "date": day.isoformat(),
        "library_tracks": len(library),
        "rows_written": written,
        "unchanged": unchanged,
    }
