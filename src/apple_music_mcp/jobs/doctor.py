"""Daily doctor (guide §6.3): the read-only live checks, recorded, with an alert on failure."""

from __future__ import annotations

from datetime import UTC, datetime

from ..commands.doctor import DEFAULT_CHECKS, run_checks
from ..container import AppContainer
from ..db.repositories import JobOutcome
from ..token_expiry import USER_TOKEN_EXPIRY_KEY, user_token_expiry
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
    await remind_token_expiry(app, outcome, notifier)


async def remind_token_expiry(app: AppContainer, outcome: JobOutcome, notifier: Notifier) -> None:
    """Guide §5: warn a few days before the session token expires, and again once it has."""
    expiry = user_token_expiry(app.state)
    if expiry is None:
        return
    days = int(expiry["days_left"])
    outcome.detail["user_token_days_left"] = days
    if days > app.settings.token_reminder_days:
        return
    today = datetime.now(UTC).date().isoformat()
    if expiry.get("reminder_sent_at", "") and str(expiry["reminder_sent_at"]).startswith(today):
        return  # one reminder per day at most
    estimated = " (estimated date)" if expiry.get("estimated") else ""
    if days < 0:
        title, body = "Apple Music login expired", "Run `apple-music-mcp login` on the Mac."
    else:
        title = f"Apple Music login expires in {days} day(s){estimated}"
        body = "Run `apple-music-mcp login` on the Mac before it expires."
    if await notifier.send(title, body, priority="high" if days < 2 else "default", tags=("key",)):
        stored = app.state.get(USER_TOKEN_EXPIRY_KEY) or {}
        app.state.set(USER_TOKEN_EXPIRY_KEY, {**stored, "reminder_sent_at": datetime.now(UTC).isoformat()})
    outcome.detail["token_reminder"] = title
