"""SQLite persistence: migrations match the schema, constraints hold, legacy state.json is imported."""

from __future__ import annotations

import json
import stat
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import insert, inspect, select
from sqlalchemy.exc import IntegrityError

from apple_music_mcp.config import STATE_FILE, Credentials
from apple_music_mcp.db import Database
from apple_music_mcp.db.schema import blacklist_entry, job_run, metadata, play_snapshot, track
from apple_music_mcp.db.settings import IMPORTED_SUFFIX, SettingsStore, import_state_file

from .conftest import build_app

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = Database.open(tmp_path / "test.db")
    yield database
    database.dispose()


def test_migrations_produce_exactly_the_schema(db: Database) -> None:
    with db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == [], "schema.py and the migrations drifted apart: add a migration"
    assert db.is_current()


def test_every_erd_table_exists(db: Database) -> None:
    tables = set(inspect(db.engine).get_table_names()) - {"alembic_version"}
    assert tables == set(metadata.tables)
    assert len(tables) == 16  # 14 domain tables + setting + http_cache (docs/data-model.md)


def test_upgrade_is_idempotent_and_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    Database.open(path).dispose()
    Database.open(path).dispose()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_foreign_keys_and_checks_are_enforced(db: Database) -> None:
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        conn.execute(
            insert(play_snapshot).values(
                snapshot_date=date(2026, 11, 6),
                track_id=999,
                played_count=1,
                skipped_count=0,
                favorited=False,
                disliked=False,
            )
        )
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        conn.execute(insert(job_run).values(job="not-a-job", started_at=NOW, status="ok"))


def test_blacklist_needs_exactly_one_target(db: Database) -> None:
    with db.engine.begin() as conn:
        tid = conn.execute(
            insert(track).values(match_key="a|b", title="b", artist_name="a").returning(track.c.id)
        ).scalar_one()
        conn.execute(
            insert(blacklist_entry).values(
                scope="track", track_id=tid, owner_request="never X", created_at=NOW
            )
        )
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        conn.execute(
            insert(blacklist_entry).values(scope="artist", track_id=tid, owner_request="x", created_at=NOW)
        )


def test_settings_store_roundtrip(db: Database) -> None:
    store = SettingsStore(db)
    assert store.get("missing", "default") == "default"
    store.set("playlist_edit_strategy", "in_place")
    store.set("playlist_edit_strategy", "rebuild")
    store.set("bpm_cache", {"1001": {"bpm": 126}})
    assert store.get("playlist_edit_strategy") == "rebuild"
    assert store.get("bpm_cache") == {"1001": {"bpm": 126}}
    assert store.keys() == ["bpm_cache", "playlist_edit_strategy"]


def test_legacy_state_file_is_imported_once_and_kept_as_backup(db: Database, tmp_path: Path) -> None:
    legacy = tmp_path / STATE_FILE
    legacy.write_text(json.dumps({"playlist_edit_strategy": "in_place", "bpm_cache": {"1": {"bpm": 90}}}))
    store = SettingsStore(db)
    store.set("bpm_cache", {"newer": True})  # the database is newer than the file

    assert sorted(import_state_file(store, legacy)) == ["playlist_edit_strategy"]
    assert store.get("bpm_cache") == {"newer": True}
    assert not legacy.exists()
    assert (tmp_path / (STATE_FILE + IMPORTED_SUFFIX)).exists()  # never deleted
    assert import_state_file(store, legacy) == []


async def test_container_uses_the_database(isolated_config: Path, logged_in_creds: Credentials) -> None:
    (isolated_config / STATE_FILE).write_text(json.dumps({"playlist_edit_strategy": "in_place"}))
    async with build_app(logged_in_creds) as app:
        assert app.state.get("playlist_edit_strategy") == "in_place"
        app.state.set("x", 1)
        assert app.db is not None
        with app.db.engine.connect() as conn:
            assert conn.execute(select(play_snapshot)).all() == []
