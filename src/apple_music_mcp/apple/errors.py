"""Exception hierarchy for everything that talks to Apple."""

from __future__ import annotations

from typing import Any


class AppleMusicError(RuntimeError):
    """An Apple endpoint answered with an error (or not at all)."""

    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class AuthError(AppleMusicError):
    """Missing, expired or rejected credentials. The message tells the user how to fix it."""


class TokenScrapeError(AuthError):
    """The developer token could not be extracted from the music.apple.com web player."""


USER_TOKEN_HELP = (
    "Apple Music user token missing or expired. Run `apple-music-mcp login` on your Mac "
    "(or set APPLE_MUSIC_USER_TOKEN) with the `media-user-token` cookie from music.apple.com."
)
DEV_TOKEN_HELP = "Apple Music rejected the developer token. Re-run `apple-music-mcp login`."
