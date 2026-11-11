"""Run one scheduled job: honour pause mode, record the run, alert on failure."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..container import AppContainer
from ..db.repositories import JobOutcome, JobRunRepository
from ..pause import pause_status


class Notifier(Protocol):
    async def send(
        self, title: str, message: str, *, priority: str = "default", tags: tuple[str, ...] = ()
    ) -> bool:
        """Deliver an alert. Returns False when it could not be sent; never raises."""
        ...


class NullNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(
        self, title: str, message: str, *, priority: str = "default", tags: tuple[str, ...] = ()
    ) -> bool:
        self.sent.append((title, message, priority))
        return False


JobFn = Callable[[AppContainer, int, JobOutcome, Notifier], Awaitable[None]]


def _registry() -> dict[str, JobFn]:
    from .backup import backup_job
    from .doctor import doctor_job
    from .heartbeat import heartbeat_job
    from .recent import recent_played_job
    from .refresh_plan import refresh_plan_job
    from .snapshot import snapshot_job

    return {
        "doctor": doctor_job,
        "backup": backup_job,
        "snapshot": snapshot_job,
        "recent_played": recent_played_job,
        "heartbeat": heartbeat_job,
        "refresh_plan": refresh_plan_job,
    }


log = logging.getLogger(__name__)

JOB_NAMES = ("doctor", "backup", "snapshot", "recent_played", "heartbeat", "refresh_plan")


async def run_job(name: str, app: AppContainer, notifier: Notifier | None = None) -> JobOutcome:
    """Run ``name`` once. A failure is recorded, alerted and re-raised as a non-zero exit by the CLI."""
    jobs = _registry()
    if name not in jobs:
        raise ValueError(f"unknown job {name!r}; choose one of {', '.join(JOB_NAMES)}")
    notifier = notifier or NullNotifier()
    runs = JobRunRepository(app.db)
    paused = pause_status(app.state)
    if paused and name != "heartbeat":  # the heartbeat itself reports the pause
        log.info("job %s skipped: paused", name)
        runs.skipped(name, f"paused until {paused.get('until') or 'resumed'}")
        return JobOutcome(status="skipped_paused", detail={"paused": paused})
    with runs.run(name) as (run_id, outcome):
        log.info("job %s started (run %s)", name, run_id)
        try:
            await jobs[name](app, run_id, outcome, notifier)
            log.info("job %s finished: %s %s", name, outcome.status, outcome.detail)
        except Exception as exc:
            log.exception("job %s failed", name)
            await notifier.send(
                f"apple-music-mcp: {name} failed",
                f"{type(exc).__name__}: {exc}"[:300],
                priority="high",
                tags=("x",),
            )
            raise
    return outcome
