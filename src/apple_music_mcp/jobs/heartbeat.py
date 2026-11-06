"""Heartbeat every few minutes (guide §6.1): ping healthchecks.io; report a closed Claude Desktop.

If the Mac is off or asleep, the pings stop and healthchecks.io alerts by e-mail (and ntfy, if the
owner connected it there). While paused, no ping is sent: pause the check too (`pause` does it when
HEALTHCHECKS_API_KEY is set).
"""

from __future__ import annotations

from collections.abc import Callable

from ..container import AppContainer
from ..db.repositories import JobOutcome
from ..monitoring import Healthchecks, claude_desktop_running
from ..pause import pause_status
from .core import Notifier

DesktopProbe = Callable[[], bool | None]


async def heartbeat_job(
    app: AppContainer,
    run_id: int,
    outcome: JobOutcome,
    notifier: Notifier,
    desktop_running: DesktopProbe = claude_desktop_running,
) -> None:
    if pause_status(app.state):
        outcome.status = "skipped_paused"
        outcome.detail = {"pinged": False, "reason": "paused"}
        return
    desktop = desktop_running()
    healthy = desktop is not False
    outcome.detail = {"claude_desktop_running": desktop}
    if not healthy:
        outcome.status = "warn"
    url = app.settings.healthcheck_url
    if not url:
        outcome.detail.update({"pinged": False, "reason": "HEALTHCHECK_URL not set"})
        return
    hc = Healthchecks(app.http, url, app.settings.healthchecks_api_key)
    message = "ok" if healthy else "Claude Desktop is not running: Dispatch from the iPhone will not work."
    outcome.detail["pinged"] = await hc.ping(ok=healthy, message=message)
