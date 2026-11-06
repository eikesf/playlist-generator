"""`apple-music-mcp pause [--until YYYY-MM-DD]` / `resume` (guide §6.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from ..db import Database
from ..db.settings import SettingsStore
from ..monitoring import Healthchecks
from ..pause import pause, resume
from ..settings import AppSettings
from . import Output


async def run_pause(until: str | None, reason: str | None, *, out: Output = print) -> int:
    end: datetime | None = None
    if until:
        try:
            day = date.fromisoformat(until)
        except ValueError:
            out("--until must be YYYY-MM-DD")
            return 2
        end = datetime(day.year, day.month, day.day, 23, 59, tzinfo=UTC)
    db = Database.open()
    try:
        pause(SettingsStore(db), end, reason)
    finally:
        db.dispose()
    out(
        "Paused: scheduled jobs will skip"
        + (f" until {until}." if until else " until `apple-music-mcp resume`.")
    )
    settings = AppSettings.from_env()
    if settings.healthcheck_url:
        import httpx

        async with httpx.AsyncClient() as http:
            paused = await Healthchecks(http, settings.healthcheck_url, settings.healthchecks_api_key).pause()
        out(
            "healthchecks.io check paused."
            if paused
            else "Pause the check on healthchecks.io too (or set HEALTHCHECKS_API_KEY), or it will alert."
        )
    return 0


async def run_resume(*, out: Output = print) -> int:
    db = Database.open()
    try:
        resume(SettingsStore(db))
    finally:
        db.dispose()
    out("Resumed.")
    settings = AppSettings.from_env()
    if settings.healthcheck_url:
        import httpx

        async with httpx.AsyncClient() as http:
            # A ping un-pauses a paused healthchecks.io check.
            ok = await Healthchecks(http, settings.healthcheck_url).ping(message="resumed")
        out("healthchecks.io pinged (check resumed)." if ok else "Could not reach healthchecks.io.")
    return 0
