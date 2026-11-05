"""Developer token: discovery from the public web player, caching and refresh.

The music.apple.com web player ships a long-lived developer JWT inside its main JavaScript
bundle. ``scrape_developer_token`` fetches the HTML, finds ``/assets/index*.js`` bundles and pulls
the first valid, non-expired JWT. ``DeveloperTokenProvider`` caches it in the credentials and
re-scrapes when it expires or Apple rejects it (rotation).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from urllib.parse import unquote, urljoin

import httpx

from ..config import Credentials, CredentialStore, is_jwt, jwt_expiry
from .errors import TokenScrapeError
from .http import BROWSER_UA

WEB_PLAYER_URLS = ("https://music.apple.com/us/browse", "https://music.apple.com/")

_BUNDLE_RE = re.compile(r"""["'(]?(/assets/index[^"'\s)]*\.js)""")
_SCRIPT_RE = re.compile(r"""["'(`]((?:\.{1,2}/|/)?(?:assets/)?[\w.~-]+\.js)["'`)]""")
# Any compact JWT: base64url('{"') == 'eyJ'. The header may start with "alg", "kid" or "typ".
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
MAX_SCRIPTS = 40  # bounded crawl of the web player's own JS chunks (fetched only on cache miss)
PRIORITY_HINTS = ("index", "musickit", "app", "main", "config", "env", "bootstrap", "vendor")

TokenScraper = Callable[[httpx.AsyncClient], Awaitable[str]]


def find_bundle_paths(html: str) -> list[str]:
    """Unique bundle paths, modern bundle first and ``index-legacy`` last."""
    seen: list[str] = []
    for match in _BUNDLE_RE.finditer(html):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return sorted(seen, key=lambda p: "legacy" in p)


def find_script_refs(text: str) -> list[str]:
    """JS file references inside HTML or a JS chunk (``/assets/x.js``, ``./x~hash.js``)."""
    seen: list[str] = []
    for match in _SCRIPT_RE.finditer(text):
        ref = match.group(1)
        if ref not in seen and not ref.startswith("//"):
            seen.append(ref)
    return seen


def find_jwt(text: str, now: float | None = None) -> str | None:
    """First valid, non-expired JWT in ``text`` (also checks URL-encoded config blobs)."""
    now = now if now is not None else time.time()
    for candidate_text in (text, unquote(text)) if "%22" in text else (text,):
        for match in _JWT_RE.finditer(candidate_text):
            token = match.group(0)
            exp = jwt_expiry(token)
            if is_jwt(token) and exp is not None and exp > now:
                return token
    return None


def _priority(url: str) -> tuple[int, int]:
    name = url.rsplit("/", 1)[-1].lower()
    hint = next((i for i, h in enumerate(PRIORITY_HINTS) if h in name), len(PRIORITY_HINTS))
    return (1 if "legacy" in name else 0, hint)


async def scrape_developer_token(http: httpx.AsyncClient) -> str:
    """Find the web player's developer JWT: page HTML first, then its JS bundles and chunks."""
    headers = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml"}
    errors: list[str] = []
    for page_url in WEB_PLAYER_URLS:
        try:
            resp = await http.get(page_url, headers=headers)
        except httpx.HTTPError as exc:
            errors.append(f"{page_url}: {exc.__class__.__name__}")
            continue
        if resp.status_code != 200:
            errors.append(f"{page_url}: HTTP {resp.status_code}")
            continue
        token = find_jwt(resp.text)
        if token:
            return token
        queue = [urljoin(page_url, ref) for ref in find_script_refs(resp.text)]
        if not queue:
            errors.append(f"{page_url}: no JavaScript bundle referenced")
            continue
        visited: set[str] = set()
        while queue and len(visited) < MAX_SCRIPTS:
            queue.sort(key=_priority)
            js_url = queue.pop(0)
            if js_url in visited or not js_url.startswith("https://music.apple.com/"):
                continue
            visited.add(js_url)
            try:
                js = await http.get(js_url, headers={"User-Agent": BROWSER_UA})
            except httpx.HTTPError as exc:
                errors.append(f"{js_url}: {exc.__class__.__name__}")
                continue
            if js.status_code != 200:
                errors.append(f"{js_url}: HTTP {js.status_code}")
                continue
            token = find_jwt(js.text)
            if token:
                return token
            queue += [u for u in (urljoin(js_url, r) for r in find_script_refs(js.text)) if u not in visited]
        errors.append(f"{page_url}: no valid JWT in {len(visited)} script(s) checked")
    raise TokenScrapeError(
        "Could not extract the Apple Music developer token from the web player. "
        "Set APPLE_MUSIC_DEVELOPER_TOKEN manually (see README). Details: " + "; ".join(errors)
    )


class DeveloperTokenProvider:
    """Returns a valid developer token, scraping and persisting a new one when needed."""

    def __init__(
        self,
        credentials: Credentials,
        http: httpx.AsyncClient,
        store: CredentialStore | None = None,
        scraper: TokenScraper = scrape_developer_token,
    ):
        self._credentials = credentials
        self._http = http
        self.store = store  # None = never write to disk (used by `login` before validation)
        self._scraper = scraper
        self._lock = asyncio.Lock()

    async def get(self, force_refresh: bool = False) -> str:
        async with self._lock:
            if not force_refresh and self._credentials.developer_token_valid():
                return self._credentials.developer_token  # type: ignore[return-value]
            token = await self._scraper(self._http)
            self._credentials.developer_token = token
            if self.store:
                self.store.save(self._credentials)
            return token
