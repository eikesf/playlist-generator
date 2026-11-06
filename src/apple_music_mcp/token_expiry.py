"""When the owner's ``media-user-token`` expires (guide §5, risk assessment §2.3).

The cookie's *Expires* date is the source of truth: ``login --browser`` reads it from the browser and
``login`` asks for it. Without it we estimate 180 days from the login, the lifetime Apple and Music
Assistant document, and say so (``estimated: true``). Only the date is stored, never the token.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from .ports import KeyValueStore

USER_TOKEN_EXPIRY_KEY = "user_token_expiry"
ESTIMATED_LIFETIME = timedelta(days=180)


def parse_expiry(text: str) -> datetime | None:
    """Accept ``YYYY-MM-DD`` (end of that day, UTC) or an ISO timestamp. None when empty or invalid."""
    text = text.strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            return datetime(day.year, day.month, day.day, 23, 59, tzinfo=UTC)
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def record_user_token_expiry(
    state: KeyValueStore, expires_at: datetime | None, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    estimated = expires_at is None
    value = {
        "expires_at": (expires_at or now + ESTIMATED_LIFETIME).isoformat(),
        "estimated": estimated,
        "recorded_at": now.isoformat(),
        "reminder_sent_at": None,
    }
    state.set(USER_TOKEN_EXPIRY_KEY, value)
    return value


def user_token_expiry(state: KeyValueStore, now: datetime | None = None) -> dict[str, Any] | None:
    """The stored expiry plus ``days_left`` (negative once expired), or None if never recorded."""
    raw = state.get(USER_TOKEN_EXPIRY_KEY)
    if not isinstance(raw, dict) or not raw.get("expires_at"):
        return None
    expires = datetime.fromisoformat(raw["expires_at"])
    now = now or datetime.now(UTC)
    return {**raw, "days_left": (expires - now).days}
