"""Daily backup job (guide §6.5)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..backup import run_backup
from ..container import AppContainer
from ..db.repositories import JobOutcome
from .core import Notifier


async def backup_job(app: AppContainer, run_id: int, outcome: JobOutcome, notifier: Notifier) -> None:
    day = datetime.now(ZoneInfo(app.settings.timezone)).date()
    result = run_backup(
        app.db.path,
        app.settings.backup_dir,
        day,
        keep_daily=app.settings.backup_keep_daily,
        keep_weekly=app.settings.backup_keep_weekly,
    )
    outcome.detail = {
        "file": result.path.name,
        "folder": str(result.path.parent),
        "size_bytes": result.size_bytes,
        "kept": len(result.kept),
        "removed": result.removed,
    }
