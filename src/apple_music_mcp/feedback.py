""" "Não gostei" and the blacklist (guide §7.8, §7.9).

* A dislike is a **removal suggestion** with the owner's reason. Nothing is removed automatically,
  and a dislike never blocks the artist: one track says nothing about the rest of their work.
* The blacklist only exists by explicit request ("nunca mais coloca X"), stored verbatim. It is
  never inferred from dislikes. Blocked artists and tracks are kept out of every playlist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select, update

from .db.database import Database
from .db.history import row_dict
from .db.repositories import TrackInfo, TrackRepository, utcnow
from .db.schema import artist, blacklist_entry, managed_playlist, track, track_feedback
from .text import artist_names, primary_artist


class FeedbackRepository:
    def __init__(self, db: Database):
        self._db = db
        self._tracks = TrackRepository(db)

    def dislike(
        self,
        info: TrackInfo,
        reason: str | None,
        *,
        playlist_id: str | None = None,
        session_id: int | None = None,
    ) -> int:
        with self._db.engine.begin() as conn:
            managed_id = None
            if playlist_id:
                managed_id = conn.execute(
                    select(managed_playlist.c.id).where(managed_playlist.c.apple_playlist_id == playlist_id)
                ).scalar()
            return int(
                conn.execute(
                    insert(track_feedback)
                    .values(
                        track_id=self._tracks.upsert(info, conn),
                        managed_playlist_id=managed_id,
                        session_id=session_id,
                        kind="disliked",
                        reason=reason,
                        removal_status="suggested",
                        created_at=utcnow(),
                    )
                    .returning(track_feedback.c.id)
                ).scalar_one()
            )

    def resolve(self, feedback_id: int, removal_status: str) -> bool:
        if removal_status not in ("removed", "kept"):
            raise ValueError("removal_status must be 'removed' or 'kept'")
        with self._db.engine.begin() as conn:
            return bool(
                conn.execute(
                    update(track_feedback)
                    .where(track_feedback.c.id == feedback_id)
                    .values(removal_status=removal_status)
                ).rowcount
            )

    def list(self, open_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        q = (
            select(
                track_feedback.c.id,
                track_feedback.c.reason,
                track_feedback.c.removal_status,
                track_feedback.c.created_at,
                track.c.catalog_id,
                track.c.artist_name,
                track.c.title,
            )
            .join(track, track.c.id == track_feedback.c.track_id)
            .order_by(track_feedback.c.created_at.desc())
            .limit(limit)
        )
        if open_only:
            q = q.where(track_feedback.c.removal_status == "suggested")
        with self._db.engine.connect() as conn:
            return [row_dict(r) for r in conn.execute(q)]


@dataclass(frozen=True)
class BlockedItem:
    id: int
    scope: str
    artist_key: str | None
    catalog_id: str | None
    match_key: str | None
    label: str
    owner_request: str


class BlacklistRepository:
    def __init__(self, db: Database):
        self._db = db
        self._tracks = TrackRepository(db)

    def add_artist(self, name: str, owner_request: str) -> int:
        with self._db.engine.begin() as conn:
            key = primary_artist(name)
            artist_id = conn.execute(select(artist.c.id).where(artist.c.name_key == key)).scalar()
            if artist_id is None:
                artist_id = conn.execute(
                    insert(artist).values(name=name, name_key=key).returning(artist.c.id)
                ).scalar_one()
            return self._insert(conn, scope="artist", artist_id=artist_id, owner_request=owner_request)

    def add_track(self, info: TrackInfo, owner_request: str) -> int:
        with self._db.engine.begin() as conn:
            return self._insert(
                conn, scope="track", track_id=self._tracks.upsert(info, conn), owner_request=owner_request
            )

    @staticmethod
    def _insert(conn: Any, **values: Any) -> int:
        return int(
            conn.execute(
                insert(blacklist_entry).values(created_at=utcnow(), **values).returning(blacklist_entry.c.id)
            ).scalar_one()
        )

    def lift(self, entry_id: int) -> bool:
        """Lifting keeps the row (history); it just stops blocking."""
        with self._db.engine.begin() as conn:
            return bool(
                conn.execute(
                    update(blacklist_entry)
                    .where(blacklist_entry.c.id == entry_id, blacklist_entry.c.lifted_at.is_(None))
                    .values(lifted_at=utcnow())
                ).rowcount
            )

    def active(self) -> list[BlockedItem]:
        q = (
            select(
                blacklist_entry.c.id,
                blacklist_entry.c.scope,
                blacklist_entry.c.owner_request,
                artist.c.name.label("artist_name"),
                artist.c.name_key,
                track.c.catalog_id,
                track.c.match_key,
                track.c.artist_name.label("track_artist"),
                track.c.title,
            )
            .select_from(
                blacklist_entry.outerjoin(artist, artist.c.id == blacklist_entry.c.artist_id).outerjoin(
                    track, track.c.id == blacklist_entry.c.track_id
                )
            )
            .where(blacklist_entry.c.lifted_at.is_(None))
            .order_by(blacklist_entry.c.id)
        )
        with self._db.engine.connect() as conn:
            return [
                BlockedItem(
                    id=r.id,
                    scope=r.scope,
                    artist_key=r.name_key,
                    catalog_id=r.catalog_id,
                    match_key=r.match_key,
                    label=r.artist_name if r.scope == "artist" else f"{r.track_artist} - {r.title}",
                    owner_request=r.owner_request,
                )
                for r in conn.execute(q)
            ]


class Blacklist:
    """Fast membership checks over the active entries."""

    def __init__(self, items: list[BlockedItem]):
        self.items = items
        self._artists = {i.artist_key for i in items if i.scope == "artist" and i.artist_key}
        self._ids = {i.catalog_id for i in items if i.scope == "track" and i.catalog_id}
        self._keys = {i.match_key for i in items if i.scope == "track" and i.match_key}

    def blocked_reason(self, song_id: str, artist_name: str, title: str) -> str | None:
        from .db.repositories import match_key

        if song_id in self._ids or match_key(artist_name, title) in self._keys:
            return "track on the blacklist"
        names = set(artist_names(artist_name)) | {primary_artist(artist_name)}
        if names & self._artists:
            return "artist on the blacklist"
        return None

    def blocks_artist(self, artist_name: str) -> bool:
        return bool((set(artist_names(artist_name)) | {primary_artist(artist_name)}) & self._artists)
