"""Placeholder replaced by the monitoring PR (healthchecks.io ping + Claude Desktop check)."""

from __future__ import annotations

from ..container import AppContainer
from ..db.repositories import JobOutcome
from .core import Notifier


async def heartbeat_job(app: AppContainer, run_id: int, outcome: JobOutcome, notifier: Notifier) -> None:
    outcome.detail = {"pinged": False, "reason": "monitoring not configured yet"}
