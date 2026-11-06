"""Repository layer (guide §4): the only code that writes domain rows with SQL.

Kept small and explicit. Jobs and services receive these objects; nothing else imports SQLAlchemy.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Connection, and_, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..text import normalize, primary_artist
from .database import Database
from .schema import artist, job_run, play_snapshot, recent_play, track


def utcnow() -> datetime:
    return datetime.now(UTC)


def match_key(artist_name: str, title: str) -> str:
    return f"{primary_artist(artist_name)}|{normalize(title)}"


@dataclass
class TrackInfo:
    """What a source knows about a track. Every id is optional; at least one or the names identify it."""

    title: str
    artist_name: str
    catalog_id: str | None = None
    library_id: str | None = None
    music_app_pid: str | None = None
    album: str | None = None
    genre: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    explicit: bool | None = None


class TrackRepository:
    def __init__(self, db: Database):
        self._db = db

    def upsert(self, info: TrackInfo, conn: Connection | None = None) -> int:
        """Return the track id, creating or enriching the row. Ids win over the name match."""
        if conn is None:
            with self._db.engine.begin() as c:
                return self.upsert(info, c)
        key = match_key(info.artist_name, info.title)
        row_id = self._find(conn, info, key)
        values: dict[str, Any] = {
            "title": info.title,
            "artist_name": info.artist_name,
            "match_key": key,
            "metadata_fetched_at": utcnow(),
        }
        for name in ("catalog_id", "library_id", "music_app_pid", "album", "genre", "duration_ms", "isrc"):
            value = getattr(info, name)
            if value is not None:
                values[name] = value
        if info.explicit is not None:
            values["explicit"] = info.explicit
        if row_id is None:
            values["artist_id"] = self._artist_id(conn, info.artist_name)
            return int(conn.execute(insert(track).values(**values).returning(track.c.id)).scalar_one())
        conn.execute(update(track).where(track.c.id == row_id).values(**values))
        return row_id

    def _find(self, conn: Connection, info: TrackInfo, key: str) -> int | None:
        for column, value in (
            (track.c.music_app_pid, info.music_app_pid),
            (track.c.catalog_id, info.catalog_id),
            (track.c.library_id, info.library_id),
        ):
            if value:
                found = conn.execute(select(track.c.id).where(column == value)).scalar()
                if found is not None:
                    return int(found)
        # Name match only joins rows that don't already carry a conflicting id of the same kind.
        query = select(track.c.id, track.c.catalog_id, track.c.music_app_pid).where(track.c.match_key == key)
        for row in conn.execute(query):
            if info.catalog_id and row.catalog_id and row.catalog_id != info.catalog_id:
                continue
            if info.music_app_pid and row.music_app_pid and row.music_app_pid != info.music_app_pid:
                continue
            return int(row.id)
        return None

    def _artist_id(self, conn: Connection, name: str) -> int:
        key = primary_artist(name)
        found = conn.execute(select(artist.c.id).where(artist.c.name_key == key)).scalar()
        if found is not None:
            return int(found)
        return int(
            conn.execute(insert(artist).values(name=name, name_key=key).returning(artist.c.id)).scalar_one()
        )

    def count(self) -> int:
        with self._db.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(track)).scalar_one())


@dataclass(frozen=True)
class Counters:
    played: int
    skipped: int
    favorited: bool
    disliked: bool


class SnapshotRepository:
    """Sparse daily counters (docs/data-model.md decision 3): a row only when something changed."""

    def __init__(self, db: Database):
        self._db = db

    def latest(self, conn: Connection) -> dict[int, Counters]:
        newest = (
            select(play_snapshot.c.track_id, func.max(play_snapshot.c.snapshot_date).label("d"))
            .group_by(play_snapshot.c.track_id)
            .subquery()
        )
        rows = conn.execute(
            select(play_snapshot).join(
                newest,
                and_(
                    play_snapshot.c.track_id == newest.c.track_id, play_snapshot.c.snapshot_date == newest.c.d
                ),
            )
        )
        return {
            r.track_id: Counters(r.played_count, r.skipped_count, bool(r.favorited), bool(r.disliked))
            for r in rows
        }

    def record(
        self, conn: Connection, day: date, job_run_id: int, counters: dict[int, Counters]
    ) -> tuple[int, int]:
        """Store changed counters for ``day``. Returns (rows written, tracks unchanged)."""
        previous = self.latest(conn)
        rows = []
        for track_id, now in counters.items():
            if previous.get(track_id) == now:
                continue
            rows.append(
                {
                    "snapshot_date": day,
                    "track_id": track_id,
                    "job_run_id": job_run_id,
                    "played_count": now.played,
                    "skipped_count": now.skipped,
                    "favorited": now.favorited,
                    "disliked": now.disliked,
                }
            )
        if rows:
            # Re-running on the same day replaces that day's rows (the job is idempotent).
            stmt = sqlite_insert(play_snapshot).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[play_snapshot.c.snapshot_date, play_snapshot.c.track_id],
                set_={
                    c: stmt.excluded[c] for c in ("played_count", "skipped_count", "favorited", "disliked")
                },
            )
            conn.execute(stmt)
        return len(rows), len(counters) - len(rows)


class RecentPlayRepository:
    def __init__(self, db: Database):
        self._db = db

    def last_run_sequence(self, conn: Connection) -> list[int]:
        last = conn.execute(
            select(job_run.c.id)
            .where(job_run.c.job == "recent_played", job_run.c.status.in_(("ok", "warn")))
            .order_by(job_run.c.started_at.desc(), job_run.c.id.desc())
            .limit(1)
        ).scalar()
        if last is None:
            return []
        return list(
            conn.execute(
                select(recent_play.c.track_id)
                .where(recent_play.c.job_run_id == last)
                .order_by(recent_play.c.position)
            ).scalars()
        )

    def record(self, conn: Connection, job_run_id: int, track_ids: Sequence[int], seen_at: datetime) -> None:
        if track_ids:
            conn.execute(
                insert(recent_play),
                [
                    {"job_run_id": job_run_id, "track_id": tid, "position": pos, "first_seen_at": seen_at}
                    for pos, tid in enumerate(track_ids)
                ],
            )


def count_new_items(previous: Sequence[int], current: Sequence[int]) -> tuple[int, bool]:
    """How many items at the top of ``current`` were played since ``previous`` was collected.

    The history is newest first. The newest item we saw last time marks the boundary: everything
    above it is new. If it is gone, the history overflowed between runs (all items are new and some
    plays were missed) and ``overflow`` is True so the schedule can be tuned.
    """
    if not previous:
        return len(current), False
    anchor = list(previous[: min(3, len(previous))])
    for start in range(len(current)):
        if list(current[start : start + len(anchor)]) == anchor:
            return start, False
    return len(current), True


@dataclass
class JobOutcome:
    status: str = "ok"
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class JobRunRepository:
    def __init__(self, db: Database):
        self._db = db

    @contextmanager
    def run(self, job: str) -> Iterator[tuple[int, JobOutcome]]:
        """Record a job run: 'running' first, then the outcome the job filled in (or 'failed')."""
        with self._db.engine.begin() as conn:
            run_id = int(
                conn.execute(
                    insert(job_run)
                    .values(job=job, started_at=utcnow(), status="running")
                    .returning(job_run.c.id)
                ).scalar_one()
            )
        outcome = JobOutcome()
        try:
            yield run_id, outcome
        except Exception as exc:
            outcome.status = "failed"
            outcome.error = f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            with self._db.engine.begin() as conn:
                conn.execute(
                    update(job_run)
                    .where(job_run.c.id == run_id)
                    .values(
                        finished_at=utcnow(),
                        status=outcome.status,
                        detail=outcome.detail,
                        error=outcome.error,
                    )
                )

    def skipped(self, job: str, reason: str) -> int:
        now = utcnow()
        with self._db.engine.begin() as conn:
            return int(
                conn.execute(
                    insert(job_run)
                    .values(
                        job=job,
                        started_at=now,
                        finished_at=now,
                        status="skipped_paused",
                        detail={"reason": reason},
                    )
                    .returning(job_run.c.id)
                ).scalar_one()
            )

    def last(self, job: str) -> dict[str, Any] | None:
        with self._db.engine.connect() as conn:
            row = conn.execute(
                select(job_run).where(job_run.c.job == job).order_by(job_run.c.id.desc()).limit(1)
            ).first()
        return dict(row._mapping) if row else None
