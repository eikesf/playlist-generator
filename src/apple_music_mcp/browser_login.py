"""Optional automatic login: open music.apple.com in a real browser window and capture tokens.

Requires the ``browser`` extra (``pip install 'apple-music-mcp[browser]'`` and
``playwright install chromium``). The user signs in normally; we read the ``media-user-token``
cookie and the developer token from the ``Authorization`` header of the page's own amp-api calls.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGIN_URL = "https://music.apple.com/{storefront}/browse"


@dataclass
class CapturedTokens:
    media_user_token: str | None = None
    developer_token: str | None = None
    media_user_token_expires: float | None = None  # unix seconds, from the cookie


def expiry_from_cookies(cookies: list[dict[str, Any]]) -> float | None:
    for cookie in cookies:
        if cookie.get("name") == "media-user-token":
            expires = cookie.get("expires")
            if isinstance(expires, (int, float)) and expires > 0:
                return float(expires)
    return None


def token_from_cookies(cookies: list[dict[str, Any]]) -> str | None:
    for cookie in cookies:
        if cookie.get("name") == "media-user-token" and cookie.get("value"):
            return str(cookie["value"])
    return None


def token_from_request(url: str, headers: dict[str, str]) -> str | None:
    if "amp-api.music.apple.com" not in url:
        return None
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    return auth[len("Bearer ") :] if auth.startswith("Bearer ") and len(auth) > 20 else None


async def capture_tokens(profile_dir: Path, storefront: str = "br", timeout: float = 300) -> CapturedTokens:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "Playwright is not installed. Run: uv tool install --force '.[browser]' && "
            "uvx playwright install chromium   (or use `apple-music-mcp login` without --browser)"
        ) from exc

    captured = CapturedTokens()
    async with async_playwright() as p:  # pragma: no cover - needs a real browser
        context = await p.chromium.launch_persistent_context(str(profile_dir), headless=False)

        def on_request(request: Any) -> None:
            token = token_from_request(request.url, request.headers)
            if token:
                captured.developer_token = token

        context.on("request", on_request)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(LOGIN_URL.format(storefront=storefront))
        print("A browser window opened. Sign in to Apple Music there (top-right 'Sign In').")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for origin in ("https://music.apple.com", "https://amp-api.music.apple.com"):
                cookies = await context.cookies(origin)
                captured.media_user_token = captured.media_user_token or token_from_cookies(cookies)
                captured.media_user_token_expires = captured.media_user_token_expires or expiry_from_cookies(
                    cookies
                )
            if captured.media_user_token and captured.developer_token:
                break
            await asyncio.sleep(1.5)
        await context.close()
    return captured
