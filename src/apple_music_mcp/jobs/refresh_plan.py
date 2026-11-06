"""Weekly refresh proposals (guide §7.10): deterministic, read-only for Apple Music, no LLM.

For every managed playlist with the refresh on whose weekly slot has passed since its last
proposal (default Monday 09:00, per playlist in ``refresh_schedule``), build a ``refresh`` proposal
and tell the owner through ntfy. Decisions happen later in a Claude conversation. A slot missed
while the Mac slept is caught up at the next run. Undecided proposals expire first, so the next
slot makes a fresh one. ``REFRESH_AUTO_APPLY=true`` accepts and applies everything (off by default).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ..apple.errors import AppleMusicError
from ..container import AppContainer
from ..db.history import ManagedPlaylistRepository, ProposalRepository
from ..db.repositories import JobOutcome
from ..db.schema import proposal
from ..settings import WEEKDAYS, AppSettings
from .core import Notifier

log = logging.getLogger(__name__)


def playlist_slot(managed: dict[str, Any], settings: AppSettings) -> tuple[str, str]:
    """(weekday, HH:MM) for a playlist: its own ``refresh_schedule`` or the global default."""
    own = managed.get("refresh_schedule") or {}
    weekday = str(own.get("weekday") or settings.refresh_weekday).lower()
    time = str(own.get("time") or settings.refresh_time)
    if weekday not in WEEKDAYS:
        weekday = settings.refresh_weekday
    return weekday, time


def last_slot(now: datetime, weekday: str, time: str) -> datetime:
    """The latest ``weekday`` at ``time`` (in ``now``'s zone) that is not after ``now``."""
    hh, _, mm = time.partition(":")
    days_back = (now.weekday() - WEEKDAYS.index(weekday)) % 7
    slot = (now - timedelta(days=days_back)).replace(
        hour=int(hh), minute=int(mm or 0), second=0, microsecond=0
    )
    return slot if slot <= now else slot - timedelta(days=7)


def due_playlists(
    managed: list[dict[str, Any]], settings: AppSettings, now: datetime, last_created: dict[int, datetime]
) -> list[dict[str, Any]]:
    due = []
    for m in managed:
        if not m.get("refresh_enabled") or m.get("status") != "active":
            continue
        slot = last_slot(now, *playlist_slot(m, settings))
        last = last_created.get(m["id"])
        if last is None or last.astimezone(slot.tzinfo) < slot:
            due.append(m)
    return due


def _last_created(app: AppContainer) -> dict[int, datetime]:
    with app.db.engine.connect() as conn:
        rows = conn.execute(
            select(proposal.c.managed_playlist_id, func.max(proposal.c.created_at))
            .where(proposal.c.kind == "refresh", proposal.c.managed_playlist_id.is_not(None))
            .group_by(proposal.c.managed_playlist_id)
        ).all()
    out = {}
    for managed_id, created in rows:
        out[int(managed_id)] = created if created.tzinfo else created.replace(tzinfo=UTC)
    return out


async def refresh_plan_job(app: AppContainer, run_id: int, outcome: JobOutcome, notifier: Notifier) -> None:
    from ..proposals import ProposalService
    from ..refresh import RefreshService

    proposals = ProposalService(app)
    repo = ProposalRepository(app.db)
    expired = proposals.expire_stale()
    now = datetime.now(ZoneInfo(app.settings.timezone))
    managed = ManagedPlaylistRepository(app.db).list()
    due = due_playlists(managed, app.settings, now, _last_created(app))
    made: list[dict[str, Any]] = []
    failed: list[str] = []
    for m in due:
        try:
            plan = await RefreshService(app, app.music_app).build_plan(
                m["apple_playlist_id"], job_run_id=run_id
            )
        except AppleMusicError as exc:
            log.warning("refresh plan for %s failed: %s", m["name"], type(exc).__name__)
            failed.append(f"{m['name']}: {type(exc).__name__}")
            continue
        if plan.get("already_open"):
            continue
        counts = plan["summary"]
        row: dict[str, Any] = {
            "proposal_id": plan["proposal_id"],
            "playlist": m["name"],
            "remove": counts["remove"],
            "add": counts["add"],
        }
        if not plan["items"]:
            repo.set_status(plan["proposal_id"], "applied")  # nothing to decide this week
            made.append(row)
            continue
        if app.settings.refresh_auto_apply:
            proposals.decide(plan["proposal_id"], accept_all=True)
            applied = await proposals.apply(plan["proposal_id"])
            row["auto_applied"] = applied["status"]
        title = f"Refresh pronto para {m['name']}: {counts['remove']} saem, {counts['add']} entram"
        body = (
            "Aplicado automaticamente (REFRESH_AUTO_APPLY)."
            if row.get("auto_applied")
            else "Abra o Claude (Mac ou Dispatch) e peça para ver as propostas pendentes. Nada mudou ainda."
        )
        if await notifier.send(title, body, tags=("musical_note",)):
            repo.mark_notified(plan["proposal_id"])
        made.append(row)
    outcome.detail = {
        "due": [m["name"] for m in due],
        "proposals": made,
        "expired": expired,
        "auto_apply": app.settings.refresh_auto_apply,
    }
    if failed:
        outcome.status = "warn"
        outcome.error = "; ".join(failed)[:500]
