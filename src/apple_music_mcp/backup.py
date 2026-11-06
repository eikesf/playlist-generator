"""Daily database backup (guide §6.5): SQLite backup API, gzip, iCloud Drive folder, 7 daily + 4 weekly.

Only files named ``apple-music-mcp-YYYY-MM-DD.db.gz`` are ever deleted by the retention rule; anything
else in the folder is left alone. Restore by hand: quit Claude Desktop, then
``gunzip -c <backup> > ~/.config/apple-music-mcp/apple-music-mcp.db``.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PREFIX = "apple-music-mcp-"
SUFFIX = ".db.gz"
NAME_RE = re.compile(rf"^{re.escape(PREFIX)}(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(SUFFIX)}$")


class BackupError(RuntimeError):
    pass


@dataclass
class BackupResult:
    path: Path
    size_bytes: int
    kept: list[str]
    removed: list[str]


def backup_database(db_path: Path, target_dir: Path, day: date) -> Path:
    """Write a consistent, verified, compressed copy of the live database. Returns the backup path."""
    if not db_path.exists():
        raise BackupError(f"database not found: {db_path}")
    if not target_dir.parent.exists():
        raise BackupError(
            f"backup folder parent does not exist: {target_dir.parent}. Is iCloud Drive turned on? "
            "Set BACKUP_DIR in ~/.config/apple-music-mcp/.env to use another folder."
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / f"{PREFIX}{day.isoformat()}{SUFFIX}"
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "copy.db"
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(raw)
        try:
            src.backup(dst)  # consistent even while the server writes (guide: never copy the open file)
        finally:
            dst.close()
            src.close()
        _verify(raw)
        packed = Path(tmp) / final.name
        with open(raw, "rb") as fin, gzip.open(packed, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)
        os.chmod(packed, 0o600)
        partial = final.with_name(final.name + ".partial")
        shutil.copyfile(packed, partial)  # copy then rename: iCloud never syncs a half-written file
        os.chmod(partial, 0o600)
        os.replace(partial, final)
    return final


def _verify(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
    if not result or result[0] != "ok":
        raise BackupError(f"backup copy failed the integrity check: {result}")


def retention(names: list[str], keep_daily: int, keep_weekly: int) -> tuple[list[str], list[str]]:
    """Split backup file names into (keep, remove), newest first.

    Keeps the newest ``keep_daily`` backups, then the newest backup of each of the next
    ``keep_weekly`` ISO weeks that the daily copies don't already cover.
    """
    dated = sorted(
        ((date.fromisoformat(m.group(1)), n) for n in names if (m := NAME_RE.match(n))), reverse=True
    )
    keep = {n for _, n in dated[:keep_daily]}
    covered = {d.isocalendar()[:2] for d, _ in dated[:keep_daily]}
    weekly = 0
    for day, name in dated[keep_daily:]:
        week = day.isocalendar()[:2]
        if week in covered or weekly >= keep_weekly:
            continue
        covered.add(week)
        keep.add(name)
        weekly += 1
    return [n for _, n in dated if n in keep], [n for _, n in dated if n not in keep]


def run_backup(
    db_path: Path, target_dir: Path, day: date, keep_daily: int = 7, keep_weekly: int = 4
) -> BackupResult:
    path = backup_database(db_path, target_dir, day)
    names = [p.name for p in target_dir.iterdir() if NAME_RE.match(p.name)]
    keep, remove = retention(names, keep_daily, keep_weekly)
    for name in remove:
        (target_dir / name).unlink(missing_ok=True)
    return BackupResult(path=path, size_bytes=path.stat().st_size, kept=keep, removed=remove)
