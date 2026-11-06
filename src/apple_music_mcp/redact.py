"""Last line of defence: strip anything token-shaped from text before it is logged or sent out."""

from __future__ import annotations

import re

_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),  # JWT
    re.compile(r"(?i)(media-user-token|authorization|x-api-key|bearer)([\"'=:\s]+)[^\s\"',;]+"),
    re.compile(r"[A-Za-z0-9+/_-]{60,}={0,2}"),  # long opaque blobs (the media-user-token is ~200+ chars)
)


def _opaque(match: re.Match[str]) -> str:
    """Redact a long run only when it looks random (mixed case and digits), not a file path or a word."""
    text = match.group(0)
    looks_random = (
        any(c.isdigit() for c in text) and any(c.isupper() for c in text) and any(c.islower() for c in text)
    )
    return "<redacted>" if looks_random else text


def redact(text: str) -> str:
    text = _PATTERNS[0].sub("<redacted-jwt>", text)
    text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    return _PATTERNS[2].sub(_opaque, text)
