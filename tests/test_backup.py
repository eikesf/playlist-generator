"""Guide §6.5: consistent compressed backups with 7 daily + 4 weekly retention."""

from __future__ import annotations

import gzip
import sqlite3
import stat
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from apple_music_mcp.backup import BackupError, retention, run_backup
from apple_music_mcp.config import Credentials
from apple_music_mcp.db import Database
from apple_music_mcp.db.settings import SettingsStore
from apple_music_mcp.jobs import run_job
from apple_music_mcp.settings import AppSettings

from .conftest import build_app


def name(d: date) -> str:
    return f"apple-music-mcp-{d.isoformat()}.db.gz"


def test_retention_keeps_7_daily_and_4_weekly() -> None:
    today = date(2026, 11, 30)  # a Monday
    names = [name(today - timedelta(days=i)) for i in range(60)] + ["notes.txt", "apple-music-mcp-x.db.gz"]
    keep, remove = retention(names, keep_daily=7, keep_weekly=4)
    assert keep[:7] == [name(today - timedelta(days=i)) for i in range(7)]
    weekly = keep[7:]
    assert len(weekly) == 4
    weeks = {date.fromisoformat(n[16:26]).isocalendar()[:2] for n in weekly}
    assert len(weeks) == 4
    assert "notes.txt" not in remove and "apple-music-mcp-x.db.gz" not in remove  # never touch other files
    assert len(keep) + len(remove) == 60


def test_backup_is_a_valid_compressed_copy_and_old_files_are_pruned(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "live.db")
    SettingsStore(db).set("hello", "world")
    target = tmp_path / "icloud" / "backups"
    (tmp_path / "icloud").mkdir()
    for i in range(1, 20):
        old = target / name(date(2026, 11, 6) - timedelta(days=i))
        target.mkdir(exist_ok=True)
        old.write_bytes(b"old")
    result = run_backup(db.path, target, date(2026, 11, 6))
    assert result.path.name == name(date(2026, 11, 6))
    assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(result.path.read_bytes()))
    conn = sqlite3.connect(restored)
    assert conn.execute("SELECT value FROM setting WHERE key = 'hello'").fetchone() == ('"world"',)
    conn.close()
    # 20 days back from Thu 2026-11-06: 7 daily (weeks 44-45) + the 2 older ISO weeks present (42, 43)
    assert len(result.kept) == 9 and len(result.removed) == 11
    assert not list(target.glob("*.partial"))
    db.dispose()


def test_missing_icloud_folder_is_a_clear_error(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "live.db")
    with pytest.raises(BackupError, match="iCloud Drive"):
        run_backup(db.path, tmp_path / "no-icloud" / "backups", date(2026, 11, 6))
    db.dispose()


async def test_backup_job_records_the_file(logged_in_creds: Credentials, tmp_path: Path) -> None:
    settings = replace(AppSettings(), backup_dir=tmp_path / "backups")
    async with build_app(logged_in_creds, settings=settings) as app:
        outcome = await run_job("backup", app)
        assert outcome.status == "ok" and outcome.detail["file"].endswith(".db.gz")
        assert (tmp_path / "backups" / outcome.detail["file"]).exists()
