"""Collect the recently-played history (risk assessment §7: low frequency, e.g. 10:30 and 22:30)."""

from __future__ import annotations

from ..container import AppContainer
from ..db.repositories import (
    JobOutcome,
    RecentPlayRepository,
    TrackInfo,
    TrackRepository,
    count_new_items,
    utcnow,
)
from .core import Notifier


async def recent_played_job(app: AppContainer, run_id: int, outcome: JobOutcome, notifier: Notifier) -> None:
    songs, non_songs = await app.taste.recent_tracks()  # ~5 small requests (pages of 10)
    tracks = TrackRepository(app.db)
    recent = RecentPlayRepository(app.db)
    with app.db.engine.begin() as conn:
        previous = recent.last_run_sequence(conn)
        ids = [
            tracks.upsert(
                TrackInfo(
                    title=s.name,
                    artist_name=s.artist,
                    catalog_id=None if s.id.startswith("i.") else s.id,
                    library_id=s.id if s.id.startswith("i.") else None,
                    album=s.album,
                    genre=s.genres[0] if s.genres else None,
                    duration_ms=s.duration_ms,
                    isrc=s.isrc,
                    explicit=s.explicit,
                ),
                conn,
            )
            for s in songs
        ]
        new_items, overflow = count_new_items(previous, ids)
        recent.record(conn, run_id, ids, utcnow())
    outcome.detail = {
        "items_returned": len(songs),
        "non_song_items": non_songs,
        "new_items": new_items,
        "overflow": overflow,
    }
    if overflow:
        outcome.status = "warn"  # plays were probably missed: collect more often
