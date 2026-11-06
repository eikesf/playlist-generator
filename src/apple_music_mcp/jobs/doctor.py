"""Daily doctor (guide §6.3): the read-only live checks, recorded, with an alert on failure."""

from __future__ import annotations

from ..commands.doctor import DEFAULT_CHECKS, run_checks
from ..container import AppContainer
from ..db.repositories import JobOutcome
from ..token_expiry import user_token_expiry
from .core import Notifier


async def doctor_job(app: AppContainer, run_id: int, outcome: JobOutcome, notifier: Notifier) -> None:
    results = await run_checks(app, [c for c in DEFAULT_CHECKS if not c.writes])
    failed = [r for r in results if r["status"] == "FAIL"]
    warned = [r for r in results if r["status"] == "warn"]
    outcome.detail = {"checks": results}
    if failed:
        outcome.status = "failed"
        outcome.error = "; ".join(f"{r['name']}: {r['detail']}" for r in failed)[:500]
        await notifier.send(
            "apple-music-mcp: doctor failed",
            "\n".join(f"{r['name']}: {r['detail']}" for r in failed)[:900],
            priority="high",
            tags=("warning",),
        )
    elif warned:
        outcome.status = "warn"
    expiry = user_token_expiry(app.state)
    if expiry is not None:
        outcome.detail["user_token_days_left"] = expiry["days_left"]
