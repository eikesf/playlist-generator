"""Initial schema: the approved data model (docs/data-model.md, revision 2).

Revision ID: 0001
Revises: (none)
Create Date: 2026-11-06 01:56:49.587265
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.create_table(
        "artist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("catalog_artist_id", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artist")),
        sa.UniqueConstraint("catalog_artist_id", name=op.f("uq_artist_catalog_artist_id")),
    )
    with op.batch_alter_table("artist", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_artist_name_key"), ["name_key"], unique=False)

    op.create_table(
        "http_cache",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_http_cache")),
    )
    with op.batch_alter_table("http_cache", schema=None) as batch_op:
        batch_op.create_index("ix_http_cache_expires_at", ["expires_at"], unique=False)

    op.create_table(
        "job_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "job IN ('doctor', 'backup', 'snapshot', 'recent_played', 'refresh_plan', 'heartbeat')",
            name=op.f("ck_job_run_job"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'ok', 'warn', 'failed', 'skipped_paused')", name=op.f("ck_job_run_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_run")),
    )
    with op.batch_alter_table("job_run", schema=None) as batch_op:
        batch_op.create_index("ix_job_run_job_started_at", ["job", "started_at"], unique=False)

    op.create_table(
        "profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("energy_curve", sa.JSON(), nullable=True),
        sa.Column("bpm_range", sa.JSON(), nullable=True),
        sa.Column("rules", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("origin IN ('builtin', 'ai_created', 'owner')", name=op.f("ck_profile_origin")),
        sa.ForeignKeyConstraint(["parent_id"], ["profile.id"], name=op.f("fk_profile_parent_id_profile")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile")),
        sa.UniqueConstraint("slug", name=op.f("uq_profile_slug")),
    )
    op.create_table(
        "session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("request", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.CheckConstraint("channel IN ('desktop', 'dispatch')", name=op.f("ck_session_channel")),
        sa.CheckConstraint("intent IS NULL OR intent IN ('create', 'edit')", name=op.f("ck_session_intent")),
        sa.CheckConstraint("status IN ('open', 'completed', 'abandoned')", name=op.f("ck_session_status")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session")),
    )
    op.create_table(
        "setting",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_setting")),
    )
    op.create_table(
        "managed_playlist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("apple_playlist_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("known_artists_pct", sa.Float(), nullable=True),
        sa.Column("known_tracks_pct", sa.Float(), nullable=True),
        sa.Column("target_duration_min", sa.Integer(), nullable=True),
        sa.Column("target_track_count", sa.Integer(), nullable=True),
        sa.Column("refresh_enabled", sa.Boolean(), nullable=False),
        sa.Column("refresh_schedule", sa.JSON(), nullable=True),
        sa.Column("discoveries_enabled", sa.Boolean(), nullable=False),
        sa.Column("discoveries_apple_playlist_id", sa.Text(), nullable=True),
        sa.Column("archive_apple_playlist_id", sa.Text(), nullable=True),
        sa.Column("edit_strategy", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("origin IN ('created', 'adopted')", name=op.f("ck_managed_playlist_origin")),
        sa.CheckConstraint("status IN ('active', 'retired')", name=op.f("ck_managed_playlist_status")),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["profile.id"], name=op.f("fk_managed_playlist_profile_id_profile")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_managed_playlist")),
        sa.UniqueConstraint("apple_playlist_id", name=op.f("uq_managed_playlist_apple_playlist_id")),
    )
    op.create_table(
        "session_turn",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["session.id"], name=op.f("fk_session_turn_session_id_session")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_turn")),
        sa.UniqueConstraint("session_id", "seq", name="uq_session_turn_session_id_seq"),
    )
    op.create_table(
        "track",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("catalog_id", sa.Text(), nullable=True),
        sa.Column("library_id", sa.Text(), nullable=True),
        sa.Column("music_app_pid", sa.Text(), nullable=True),
        sa.Column("match_key", sa.Text(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("artist_name", sa.Text(), nullable=False),
        sa.Column("album", sa.Text(), nullable=True),
        sa.Column("genre", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("isrc", sa.Text(), nullable=True),
        sa.Column("explicit", sa.Boolean(), nullable=True),
        sa.Column("bpm", sa.Float(), nullable=True),
        sa.Column("bpm_source", sa.Text(), nullable=True),
        sa.Column("bpm_confidence", sa.Float(), nullable=True),
        sa.Column("bpm_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "bpm_source IS NULL OR bpm_source IN ('getsongbpm', 'deezer', 'estimated')",
            name=op.f("ck_track_bpm_source"),
        ),
        sa.ForeignKeyConstraint(["artist_id"], ["artist.id"], name=op.f("fk_track_artist_id_artist")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track")),
        sa.UniqueConstraint("catalog_id", name=op.f("uq_track_catalog_id")),
        sa.UniqueConstraint("library_id", name=op.f("uq_track_library_id")),
        sa.UniqueConstraint("music_app_pid", name=op.f("uq_track_music_app_pid")),
    )
    with op.batch_alter_table("track", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_track_match_key"), ["match_key"], unique=False)

    op.create_table(
        "blacklist_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("artist_id", sa.Integer(), nullable=True),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("owner_request", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lifted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(scope = 'artist' AND artist_id IS NOT NULL AND track_id IS NULL) OR (scope = 'track' AND track_id IS NOT NULL AND artist_id IS NULL)",
            name=op.f("ck_blacklist_entry_scope_target"),
        ),
        sa.ForeignKeyConstraint(
            ["artist_id"], ["artist.id"], name=op.f("fk_blacklist_entry_artist_id_artist")
        ),
        sa.ForeignKeyConstraint(["track_id"], ["track.id"], name=op.f("fk_blacklist_entry_track_id_track")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blacklist_entry")),
    )
    op.create_table(
        "play_snapshot",
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("job_run_id", sa.Integer(), nullable=True),
        sa.Column("played_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("favorited", sa.Boolean(), nullable=False),
        sa.Column("disliked", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_run_id"], ["job_run.id"], name=op.f("fk_play_snapshot_job_run_id_job_run")
        ),
        sa.ForeignKeyConstraint(["track_id"], ["track.id"], name=op.f("fk_play_snapshot_track_id_track")),
        sa.PrimaryKeyConstraint("snapshot_date", "track_id", name=op.f("pk_play_snapshot")),
    )
    op.create_table(
        "proposal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("job_run_id", sa.Integer(), nullable=True),
        sa.Column("managed_playlist_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('create', 'edit', 'refresh', 'discoveries', 'promotion')", name=op.f("ck_proposal_kind")
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'applied', 'rejected', 'expired')",
            name=op.f("ck_proposal_status"),
        ),
        sa.ForeignKeyConstraint(["job_run_id"], ["job_run.id"], name=op.f("fk_proposal_job_run_id_job_run")),
        sa.ForeignKeyConstraint(
            ["managed_playlist_id"],
            ["managed_playlist.id"],
            name=op.f("fk_proposal_managed_playlist_id_managed_playlist"),
        ),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"], name=op.f("fk_proposal_session_id_session")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal")),
    )
    with op.batch_alter_table("proposal", schema=None) as batch_op:
        batch_op.create_index("ix_proposal_status", ["status"], unique=False)

    op.create_table(
        "recent_play",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_run_id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_run_id"], ["job_run.id"], name=op.f("fk_recent_play_job_run_id_job_run")
        ),
        sa.ForeignKeyConstraint(["track_id"], ["track.id"], name=op.f("fk_recent_play_track_id_track")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recent_play")),
    )
    op.create_table(
        "track_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("managed_playlist_id", sa.Integer(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("removal_status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('disliked')", name=op.f("ck_track_feedback_kind")),
        sa.CheckConstraint(
            "removal_status IN ('suggested', 'removed', 'kept')",
            name=op.f("ck_track_feedback_removal_status"),
        ),
        sa.ForeignKeyConstraint(
            ["managed_playlist_id"],
            ["managed_playlist.id"],
            name=op.f("fk_track_feedback_managed_playlist_id_managed_playlist"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["session.id"], name=op.f("fk_track_feedback_session_id_session")
        ),
        sa.ForeignKeyConstraint(["track_id"], ["track.id"], name=op.f("fk_track_feedback_track_id_track")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_feedback")),
    )
    op.create_table(
        "playlist_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("managed_playlist_id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event IN ('added', 'removed', 'moved', 'archived')", name=op.f("ck_playlist_event_event")
        ),
        sa.CheckConstraint(
            "origin IN ('session', 'refresh', 'promotion', 'external')", name=op.f("ck_playlist_event_origin")
        ),
        sa.ForeignKeyConstraint(
            ["managed_playlist_id"],
            ["managed_playlist.id"],
            name=op.f("fk_playlist_event_managed_playlist_id_managed_playlist"),
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposal.id"], name=op.f("fk_playlist_event_proposal_id_proposal")
        ),
        sa.ForeignKeyConstraint(["track_id"], ["track.id"], name=op.f("fk_playlist_event_track_id_track")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playlist_event")),
    )
    with op.batch_alter_table("playlist_event", schema=None) as batch_op:
        batch_op.create_index(
            "ix_playlist_event_playlist_occurred", ["managed_playlist_id", "occurred_at"], unique=False
        )

    op.create_table(
        "proposal_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("bucket", sa.Text(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("replaces_item_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("action IN ('add', 'remove', 'keep')", name=op.f("ck_proposal_item_action")),
        sa.CheckConstraint(
            "bucket IS NULL OR bucket IN ('known_track', 'known_artist_new_track', 'new_artist')",
            name=op.f("ck_proposal_item_bucket"),
        ),
        sa.CheckConstraint(
            "decision IN ('pending', 'accepted', 'rejected')", name=op.f("ck_proposal_item_decision")
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposal.id"], name=op.f("fk_proposal_item_proposal_id_proposal")
        ),
        sa.ForeignKeyConstraint(
            ["replaces_item_id"],
            ["proposal_item.id"],
            name=op.f("fk_proposal_item_replaces_item_id_proposal_item"),
        ),
        sa.ForeignKeyConstraint(["track_id"], ["track.id"], name=op.f("fk_proposal_item_track_id_track")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_item")),
    )


def downgrade() -> None:

    op.drop_table("proposal_item")
    with op.batch_alter_table("playlist_event", schema=None) as batch_op:
        batch_op.drop_index("ix_playlist_event_playlist_occurred")

    op.drop_table("playlist_event")
    op.drop_table("track_feedback")
    op.drop_table("recent_play")
    with op.batch_alter_table("proposal", schema=None) as batch_op:
        batch_op.drop_index("ix_proposal_status")

    op.drop_table("proposal")
    op.drop_table("play_snapshot")
    op.drop_table("blacklist_entry")
    with op.batch_alter_table("track", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_track_match_key"))

    op.drop_table("track")
    op.drop_table("session_turn")
    op.drop_table("managed_playlist")
    op.drop_table("setting")
    op.drop_table("session")
    op.drop_table("profile")
    with op.batch_alter_table("job_run", schema=None) as batch_op:
        batch_op.drop_index("ix_job_run_job_started_at")

    op.drop_table("job_run")
    with op.batch_alter_table("http_cache", schema=None) as batch_op:
        batch_op.drop_index("ix_http_cache_expires_at")

    op.drop_table("http_cache")
    with op.batch_alter_table("artist", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_artist_name_key"))

    op.drop_table("artist")
