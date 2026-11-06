"""Repositories for playlists the system manages and everything that happened to them (guide §7.7, §8.1)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, desc, insert, select, update

from .database import Database
from .repositories import TrackInfo, TrackRepository, utcnow
from .schema import (
    managed_playlist,
    playlist_event,
    profile,
    proposal,
    proposal_item,
    session,
    session_turn,
    track,
)

MANAGED_FIELDS = (
    "name",
    "purpose",
    "known_artists_pct",
    "known_tracks_pct",
    "target_duration_min",
    "target_track_count",
    "refresh_enabled",
    "refresh_schedule",
    "discoveries_enabled",
    "discoveries_apple_playlist_id",
    "archive_apple_playlist_id",
    "edit_strategy",
    "status",
)


def row_dict(r: Any) -> dict[str, Any]:
    out = dict(r._mapping)
    for k, v in out.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
    return out


class ManagedPlaylistRepository:
    def __init__(self, db: Database):
        self._db = db

    def _profile_id(self, conn: Connection, slug: str | None) -> int | None:
        if not slug:
            return None
        found = conn.execute(select(profile.c.id).where(profile.c.slug == slug)).scalar()
        return int(found) if found is not None else None

    def upsert(
        self, apple_playlist_id: str, *, origin: str, profile_slug: str | None = None, **fields: Any
    ) -> int:
        unknown = set(fields) - set(MANAGED_FIELDS)
        if unknown:
            raise ValueError(f"unknown managed playlist fields: {sorted(unknown)}")
        now = utcnow()
        with self._db.engine.begin() as conn:
            values: dict[str, Any] = {**fields, "updated_at": now}
            if profile_slug is not None:
                values["profile_id"] = self._profile_id(conn, profile_slug)
            existing = conn.execute(
                select(managed_playlist.c.id).where(managed_playlist.c.apple_playlist_id == apple_playlist_id)
            ).scalar()
            if existing is not None:
                conn.execute(
                    update(managed_playlist).where(managed_playlist.c.id == existing).values(**values)
                )
                return int(existing)
            values.setdefault("name", apple_playlist_id)
            return int(
                conn.execute(
                    insert(managed_playlist)
                    .values(apple_playlist_id=apple_playlist_id, origin=origin, created_at=now, **values)
                    .returning(managed_playlist.c.id)
                ).scalar_one()
            )

    def move(self, old_apple_id: str, new_apple_id: str) -> None:
        """A rebuild replaced the Apple playlist: keep the managed record (and its history) on the new id."""
        with self._db.engine.begin() as conn:
            conn.execute(
                update(managed_playlist)
                .where(managed_playlist.c.apple_playlist_id == old_apple_id)
                .values(apple_playlist_id=new_apple_id, updated_at=utcnow())
            )

    def _query(self) -> Any:
        return select(managed_playlist, profile.c.slug.label("profile")).select_from(
            managed_playlist.outerjoin(profile, managed_playlist.c.profile_id == profile.c.id)
        )

    def get(self, apple_playlist_id: str) -> dict[str, Any] | None:
        with self._db.engine.connect() as conn:
            r = conn.execute(
                self._query().where(managed_playlist.c.apple_playlist_id == apple_playlist_id)
            ).first()
        return row_dict(r) if r else None

    def get_by_id(self, managed_id: int) -> dict[str, Any] | None:
        with self._db.engine.connect() as conn:
            r = conn.execute(self._query().where(managed_playlist.c.id == managed_id)).first()
        return row_dict(r) if r else None

    def list(self, include_retired: bool = False) -> list[dict[str, Any]]:
        q = self._query().order_by(managed_playlist.c.name)
        if not include_retired:
            q = q.where(managed_playlist.c.status == "active")
        with self._db.engine.connect() as conn:
            return [row_dict(r) for r in conn.execute(q)]


class SessionRepository:
    def __init__(self, db: Database):
        self._db = db

    def start(self, channel: str, intent: str | None, request: str | None) -> int:
        with self._db.engine.begin() as conn:
            return int(
                conn.execute(
                    insert(session)
                    .values(
                        started_at=utcnow(), channel=channel, intent=intent, request=request, status="open"
                    )
                    .returning(session.c.id)
                ).scalar_one()
            )

    def add_turn(self, session_id: int, question: str, answer: str | None) -> int:
        with self._db.engine.begin() as conn:
            if conn.execute(select(session.c.id).where(session.c.id == session_id)).scalar() is None:
                raise ValueError(f"unknown session {session_id}; call start_session first")
            seq = len(
                conn.execute(select(session_turn.c.id).where(session_turn.c.session_id == session_id)).all()
            )
            conn.execute(
                insert(session_turn).values(
                    session_id=session_id, seq=seq + 1, question=question, answer=answer
                )
            )
            return seq + 1

    def finish(self, session_id: int, status: str = "completed") -> None:
        with self._db.engine.begin() as conn:
            conn.execute(update(session).where(session.c.id == session_id).values(status=status))

    def get(self, session_id: int) -> dict[str, Any] | None:
        with self._db.engine.connect() as conn:
            r = conn.execute(select(session).where(session.c.id == session_id)).first()
            if r is None:
                return None
            turns = conn.execute(
                select(session_turn.c.seq, session_turn.c.question, session_turn.c.answer)
                .where(session_turn.c.session_id == session_id)
                .order_by(session_turn.c.seq)
            )
            return {**row_dict(r), "turns": [row_dict(t) for t in turns]}


@dataclass
class ProposedTrack:
    info: TrackInfo
    action: str = "add"
    position: int | None = None
    section: str | None = None
    bucket: str | None = None


class ProposalRepository:
    def __init__(self, db: Database):
        self._db = db
        self._tracks = TrackRepository(db)

    def create(
        self,
        kind: str,
        items: Sequence[ProposedTrack],
        *,
        session_id: int | None = None,
        managed_playlist_id: int | None = None,
        job_run_id: int | None = None,
        params: dict[str, Any] | None = None,
        status: str = "pending_approval",
    ) -> int:
        with self._db.engine.begin() as conn:
            pid = int(
                conn.execute(
                    insert(proposal)
                    .values(
                        kind=kind,
                        status=status,
                        session_id=session_id,
                        managed_playlist_id=managed_playlist_id,
                        job_run_id=job_run_id,
                        params=params or {},
                        created_at=utcnow(),
                    )
                    .returning(proposal.c.id)
                ).scalar_one()
            )
            for i, item in enumerate(items):
                conn.execute(
                    insert(proposal_item).values(
                        proposal_id=pid,
                        track_id=self._tracks.upsert(item.info, conn),
                        action=item.action,
                        position=item.position if item.position is not None else i,
                        section=item.section,
                        bucket=item.bucket,
                        decision="pending",
                    )
                )
            return pid

    def get(self, proposal_id: int) -> dict[str, Any] | None:
        with self._db.engine.connect() as conn:
            r = conn.execute(select(proposal).where(proposal.c.id == proposal_id)).first()
            if r is None:
                return None
            items = conn.execute(
                select(
                    proposal_item.c.id,
                    proposal_item.c.action,
                    proposal_item.c.position,
                    proposal_item.c.section,
                    proposal_item.c.bucket,
                    proposal_item.c.decision,
                    proposal_item.c.reason,
                    track.c.catalog_id,
                    track.c.title,
                    track.c.artist_name,
                    track.c.duration_ms,
                )
                .join(track, track.c.id == proposal_item.c.track_id)
                .where(proposal_item.c.proposal_id == proposal_id)
                .order_by(proposal_item.c.position)
            )
            return {**row_dict(r), "items": [row_dict(i) for i in items]}

    def decide_items(self, proposal_id: int, decisions: dict[str, tuple[str, str | None]]) -> int:
        """Set ``decision``/``reason`` by catalog id. Returns how many items changed."""
        changed = 0
        with self._db.engine.begin() as conn:
            rows = conn.execute(
                select(proposal_item.c.id, track.c.catalog_id)
                .join(track, track.c.id == proposal_item.c.track_id)
                .where(proposal_item.c.proposal_id == proposal_id)
            ).all()
            for item_id, catalog_id in rows:
                if catalog_id in decisions:
                    decision, reason = decisions[catalog_id]
                    conn.execute(
                        update(proposal_item)
                        .where(proposal_item.c.id == item_id)
                        .values(decision=decision, reason=reason)
                    )
                    changed += 1
        return changed

    def set_status(self, proposal_id: int, status: str, **extra: Any) -> None:
        values: dict[str, Any] = {"status": status, **extra}
        if status in ("approved", "rejected", "expired") and "decided_at" not in values:
            values["decided_at"] = utcnow()
        if status == "applied":
            values.setdefault("applied_at", utcnow())
        with self._db.engine.begin() as conn:
            conn.execute(update(proposal).where(proposal.c.id == proposal_id).values(**values))

    def rejected_tracks(self, limit: int = 100) -> list[dict[str, Any]]:
        """Tracks the owner turned down in earlier previews, newest first, with the reason."""
        q = (
            select(
                track.c.catalog_id,
                track.c.artist_name,
                track.c.title,
                proposal_item.c.reason,
                proposal.c.created_at,
            )
            .join(proposal_item, proposal_item.c.track_id == track.c.id)
            .join(proposal, proposal.c.id == proposal_item.c.proposal_id)
            .where(proposal_item.c.decision == "rejected")
            .order_by(desc(proposal.c.created_at))
            .limit(limit)
        )
        with self._db.engine.connect() as conn:
            return [row_dict(r) for r in conn.execute(q)]


class PlaylistEventRepository:
    """Append-only history of a managed playlist (data model decision 8)."""

    def __init__(self, db: Database):
        self._db = db
        self._tracks = TrackRepository(db)

    def record(
        self,
        managed_playlist_id: int,
        events: Sequence[tuple[TrackInfo, str, int | None]],
        *,
        origin: str,
        proposal_id: int | None = None,
        reason: str | None = None,
    ) -> int:
        now = utcnow()
        with self._db.engine.begin() as conn:
            for info, event, position in events:
                conn.execute(
                    insert(playlist_event).values(
                        managed_playlist_id=managed_playlist_id,
                        track_id=self._tracks.upsert(info, conn),
                        event=event,
                        position=position,
                        origin=origin,
                        proposal_id=proposal_id,
                        reason=reason,
                        occurred_at=now,
                    )
                )
        return len(events)

    def history(self, managed_playlist_id: int, limit: int = 100) -> list[dict[str, Any]]:
        q = (
            select(
                playlist_event.c.event,
                playlist_event.c.position,
                playlist_event.c.origin,
                playlist_event.c.reason,
                playlist_event.c.occurred_at,
                track.c.artist_name,
                track.c.title,
                track.c.catalog_id,
            )
            .join(track, track.c.id == playlist_event.c.track_id)
            .where(playlist_event.c.managed_playlist_id == managed_playlist_id)
            .order_by(desc(playlist_event.c.occurred_at), desc(playlist_event.c.id))
            .limit(limit)
        )
        with self._db.engine.connect() as conn:
            return [row_dict(r) for r in conn.execute(q)]
