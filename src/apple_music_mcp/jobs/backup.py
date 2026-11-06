"""Placeholder replaced by the backup PR."""

from __future__ import annotations

from ..container import AppContainer
from ..db.repositories import JobOutcome
from .core import Notifier


async def backup_job(app: AppContainer, run_id: int, outcome: JobOutcome, notifier: Notifier) -> None:
    outcome.status = "warn"
    outcome.detail = {"reason": "backup not implemented yet"}
