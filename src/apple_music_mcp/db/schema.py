"""Database schema (SQLAlchemy Core), mirroring the approved ERD in ``docs/data-model.md``.

Changing anything here requires a new Alembic migration in ``db/migrations/versions``.
``tests/test_db.py`` fails if the migrations and this module drift apart.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)

# Stable constraint names, so migrations can alter them later (SQLite needs batch mode for that).
metadata = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def _now_columns() -> list[Column[object]]:
    return [Column("created_at", DateTime(timezone=True), nullable=False)]


artist = Table(
    "artist",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("catalog_artist_id", Text, unique=True),
    Column("name", Text, nullable=False),
    Column("name_key", Text, nullable=False, index=True),
)

track = Table(
    "track",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("catalog_id", Text, unique=True),
    Column("library_id", Text, unique=True),
    Column("music_app_pid", Text, unique=True),
    Column("match_key", Text, nullable=False, index=True),
    Column("artist_id", ForeignKey("artist.id")),
    Column("title", Text, nullable=False),
    Column("artist_name", Text, nullable=False),
    Column("album", Text),
    Column("genre", Text),
    Column("duration_ms", Integer),
    Column("isrc", Text),
    Column("explicit", Boolean),
    Column("bpm", Float),
    Column("bpm_source", Text),
    Column("bpm_confidence", Float),
    Column("bpm_fetched_at", DateTime(timezone=True)),
    Column("metadata_fetched_at", DateTime(timezone=True)),
    CheckConstraint(
        "bpm_source IS NULL OR bpm_source IN ('getsongbpm', 'deezer', 'estimated')", name="bpm_source"
    ),
)

job_run = Table(
    "job_run",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("job", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", Text, nullable=False),
    Column("detail", JSON),
    Column("error", Text),
    CheckConstraint(
        "job IN ('doctor', 'backup', 'snapshot', 'recent_played', 'refresh_plan', 'heartbeat')", name="job"
    ),
    CheckConstraint("status IN ('running', 'ok', 'warn', 'failed', 'skipped_paused')", name="status"),
    Index("ix_job_run_job_started_at", "job", "started_at"),
)

play_snapshot = Table(
    "play_snapshot",
    metadata,
    Column("snapshot_date", Date, primary_key=True),
    Column("track_id", ForeignKey("track.id"), primary_key=True),
    Column("job_run_id", ForeignKey("job_run.id")),
    Column("played_count", Integer, nullable=False),
    Column("skipped_count", Integer, nullable=False),
    Column("favorited", Boolean, nullable=False),
    Column("disliked", Boolean, nullable=False),
)

recent_play = Table(
    "recent_play",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("job_run_id", ForeignKey("job_run.id"), nullable=False),
    Column("track_id", ForeignKey("track.id"), nullable=False),
    Column("position", Integer, nullable=False),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
)

profile = Table(
    "profile",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("parent_id", ForeignKey("profile.id")),
    Column("slug", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("origin", Text, nullable=False),
    Column("description", Text),
    Column("energy_curve", JSON),
    Column("bpm_range", JSON),
    Column("rules", JSON),
    *_now_columns(),
    CheckConstraint("origin IN ('builtin', 'ai_created', 'owner')", name="origin"),
)

managed_playlist = Table(
    "managed_playlist",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("apple_playlist_id", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("purpose", Text),
    Column("profile_id", ForeignKey("profile.id")),
    Column("origin", Text, nullable=False),
    Column("known_artists_pct", Float),
    Column("known_tracks_pct", Float),
    Column("target_duration_min", Integer),
    Column("target_track_count", Integer),
    Column("refresh_enabled", Boolean, nullable=False, default=False),
    Column("refresh_schedule", JSON),
    Column("discoveries_enabled", Boolean, nullable=False, default=False),
    Column("discoveries_apple_playlist_id", Text),
    Column("archive_apple_playlist_id", Text),
    Column("edit_strategy", Text),
    Column("status", Text, nullable=False, default="active"),
    *_now_columns(),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("origin IN ('created', 'adopted')", name="origin"),
    CheckConstraint("status IN ('active', 'retired')", name="status"),
)

session = Table(
    "session",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("channel", Text, nullable=False),
    Column("intent", Text),
    Column("request", Text),
    Column("status", Text, nullable=False, default="open"),
    CheckConstraint("channel IN ('desktop', 'dispatch')", name="channel"),
    CheckConstraint("intent IS NULL OR intent IN ('create', 'edit')", name="intent"),
    CheckConstraint("status IN ('open', 'completed', 'abandoned')", name="status"),
)

session_turn = Table(
    "session_turn",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("session_id", ForeignKey("session.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("question", Text, nullable=False),
    Column("answer", Text),
    UniqueConstraint("session_id", "seq", name="uq_session_turn_session_id_seq"),
)

proposal = Table(
    "proposal",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("session_id", ForeignKey("session.id")),
    Column("job_run_id", ForeignKey("job_run.id")),
    Column("managed_playlist_id", ForeignKey("managed_playlist.id")),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False, default="draft"),
    Column("params", JSON),
    *_now_columns(),
    Column("decided_at", DateTime(timezone=True)),
    Column("applied_at", DateTime(timezone=True)),
    Column("notified_at", DateTime(timezone=True)),
    CheckConstraint("kind IN ('create', 'edit', 'refresh', 'discoveries', 'promotion')", name="kind"),
    CheckConstraint(
        "status IN ('draft', 'pending_approval', 'approved', 'applied', 'rejected', 'expired')", name="status"
    ),
    Index("ix_proposal_status", "status"),
)

proposal_item = Table(
    "proposal_item",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("proposal_id", ForeignKey("proposal.id"), nullable=False),
    Column("track_id", ForeignKey("track.id"), nullable=False),
    Column("action", Text, nullable=False),
    Column("position", Integer),
    Column("section", Text),
    Column("bucket", Text),
    Column("decision", Text, nullable=False, default="pending"),
    Column("reason", Text),
    Column("replaces_item_id", ForeignKey("proposal_item.id")),
    CheckConstraint("action IN ('add', 'remove', 'keep')", name="action"),
    CheckConstraint(
        "bucket IS NULL OR bucket IN ('known_track', 'known_artist_new_track', 'new_artist')", name="bucket"
    ),
    CheckConstraint("decision IN ('pending', 'accepted', 'rejected')", name="decision"),
)

playlist_event = Table(
    "playlist_event",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("managed_playlist_id", ForeignKey("managed_playlist.id"), nullable=False),
    Column("track_id", ForeignKey("track.id"), nullable=False),
    Column("event", Text, nullable=False),
    Column("position", Integer),
    Column("origin", Text, nullable=False),
    Column("proposal_id", ForeignKey("proposal.id")),
    Column("reason", Text),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("event IN ('added', 'removed', 'moved', 'archived')", name="event"),
    CheckConstraint("origin IN ('session', 'refresh', 'promotion', 'external')", name="origin"),
    Index("ix_playlist_event_playlist_occurred", "managed_playlist_id", "occurred_at"),
)

track_feedback = Table(
    "track_feedback",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("track_id", ForeignKey("track.id"), nullable=False),
    Column("managed_playlist_id", ForeignKey("managed_playlist.id")),
    Column("session_id", ForeignKey("session.id")),
    Column("kind", Text, nullable=False, default="disliked"),
    Column("reason", Text),
    Column("removal_status", Text, nullable=False, default="suggested"),
    *_now_columns(),
    CheckConstraint("kind IN ('disliked')", name="kind"),
    CheckConstraint("removal_status IN ('suggested', 'removed', 'kept')", name="removal_status"),
)

blacklist_entry = Table(
    "blacklist_entry",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("scope", Text, nullable=False),
    Column("artist_id", ForeignKey("artist.id")),
    Column("track_id", ForeignKey("track.id")),
    Column("owner_request", Text, nullable=False),
    *_now_columns(),
    Column("lifted_at", DateTime(timezone=True)),
    CheckConstraint(
        "(scope = 'artist' AND artist_id IS NOT NULL AND track_id IS NULL) OR "
        "(scope = 'track' AND track_id IS NOT NULL AND artist_id IS NULL)",
        name="scope_target",
    ),
)

# Support tables (outside the domain diagram, docs/data-model.md §2).
setting = Table(
    "setting",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", JSON),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

http_cache = Table(
    "http_cache",
    metadata,
    Column("key", Text, primary_key=True),
    Column("namespace", Text, nullable=False),
    Column("payload", JSON),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Index("ix_http_cache_expires_at", "expires_at"),
)
