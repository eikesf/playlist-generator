"""Developer-token discovery from the public music.apple.com web player.

The web player ships a long-lived developer JWT inside its main JavaScript bundle. We fetch the
HTML, find candidate ``/assets/index*.js`` bundles and pull the first valid, non-expired JWT out
of them. This is unofficial: if Apple changes the bundle layout, scraping fails with
``TokenScrapeError`` and the user can paste a token manually (``APPLE_MUSIC_DEVELOPER_TOKEN``).
"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin

import httpx

from .config import is_jwt, jwt_expiry

WEB_PLAYER_URLS = ("https://music.apple.com/us/browse", "https://music.apple.com/")
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)

_BUNDLE_RE = re.compile(r"""["'(]?(/assets/index[^"'\s)]*\.js)""")
_JWT_RE = re.compile(r"eyJh[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


class TokenScrapeError(RuntimeError):
    pass


def find_bundle_paths(html: str) -> list[str]:
    """Return unique bundle paths, preferring the modern bundle over ``index-legacy``."""
    seen: list[str] = []
    for match in _BUNDLE_RE.finditer(html):
        path = match.group(1)
        if path not in seen:
            seen.append(path)
    return sorted(seen, key=lambda p: "legacy" in p)


def find_jwt(js: str, now: float | None = None) -> str | None:
    now = now if now is not None else time.time()
    for match in _JWT_RE.finditer(js):
        token = match.group(0)
        exp = jwt_expiry(token)
        if is_jwt(token) and exp is not None and exp > now:
            return token
    return None


async def scrape_developer_token(http: httpx.AsyncClient | None = None) -> str:
    owns = http is None
    http = http or httpx.AsyncClient(timeout=20, follow_redirects=True)
    headers = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml"}
    errors: list[str] = []
    try:
        for page_url in WEB_PLAYER_URLS:
            try:
                resp = await http.get(page_url, headers=headers)
            except httpx.HTTPError as exc:
                errors.append(f"{page_url}: {exc.__class__.__name__}")
                continue
            if resp.status_code != 200:
                errors.append(f"{page_url}: HTTP {resp.status_code}")
                continue
            bundles = find_bundle_paths(resp.text)
            if not bundles:
                errors.append(f"{page_url}: no /assets/index*.js bundle found")
                continue
            for path in bundles:
                js_url = urljoin(page_url, path)
                try:
                    js = await http.get(js_url, headers={"User-Agent": BROWSER_UA})
                except httpx.HTTPError as exc:
                    errors.append(f"{js_url}: {exc.__class__.__name__}")
                    continue
                if js.status_code == 200:
                    token = find_jwt(js.text)
                    if token:
                        return token
                    errors.append(f"{js_url}: no valid JWT in bundle")
                else:
                    errors.append(f"{js_url}: HTTP {js.status_code}")
    finally:
        if owns:
            await http.aclose()
    raise TokenScrapeError(
        "Could not extract the Apple Music developer token from the web player. "
        "Set APPLE_MUSIC_DEVELOPER_TOKEN manually (see README). Details: " + "; ".join(errors)
    )
