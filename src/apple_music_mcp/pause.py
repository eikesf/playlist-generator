"""Pause mode for trips (guide §6.4): scheduled jobs skip and alerts stay quiet until resumed."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .ports import KeyValueStore

PAUSE_KEY = "paused"


def pause(state: KeyValueStore, until: datetime | None, reason: str | None = None) -> dict[str, Any]:
    value = {
        "since": datetime.now(UTC).isoformat(),
        "until": until.isoformat() if until else None,
        "reason": reason,
    }
    state.set(PAUSE_KEY, value)
    return value


def resume(state: KeyValueStore) -> None:
    state.set(PAUSE_KEY, None)


def pause_status(state: KeyValueStore, now: datetime | None = None) -> dict[str, Any] | None:
    """The active pause, or None. A pause with an end date expires by itself."""
    raw = state.get(PAUSE_KEY)
    if not isinstance(raw, dict):
        return None
    until = raw.get("until")
    if until and datetime.fromisoformat(until) <= (now or datetime.now(UTC)):
        return None
    return raw
