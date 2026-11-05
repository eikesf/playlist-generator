"""Async client for the Apple Music web API (``amp-api.music.apple.com``).

Catalog calls need only the developer token. Library calls (``/v1/me/...``) also need the
``media-user-token`` cookie of a logged-in music.apple.com session.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .config import Credentials, load_credentials, save_credentials
from .models import Artist, Song
from .tokens import BROWSER_UA, TokenScrapeError, scrape_developer_token

AMP_BASE = "https://amp-api.music.apple.com"
ITUNES_SEARCH = "https://itunes.apple.com/search"
MAX_SEARCH_LIMIT = 25
RETRY_STATUSES = {429, 500, 502, 503, 504}


class AppleMusicError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class AuthError(AppleMusicError):
    """Missing, expired or rejected credentials. The message tells the user how to fix it."""


USER_TOKEN_HELP = (
    "Apple Music user token missing or expired. Run `apple-music-mcp login` on your Mac "
    "(or set APPLE_MUSIC_USER_TOKEN) with the `media-user-token` cookie from music.apple.com."
)


class AppleMusicClient:
    def __init__(
        self,
        credentials: Credentials | None = None,
        http: httpx.AsyncClient | None = None,
        *,
        persist: bool = True,
        max_retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        token_scraper: Callable[[httpx.AsyncClient], Awaitable[str]] = scrape_developer_token,
    ):
        self.credentials = credentials or load_credentials()
        self._http = http or httpx.AsyncClient(timeout=30, follow_redirects=True)
        self._owns_http = http is None
        self.persist = persist
        self.max_retries = max_retries
        self._sleep = sleep
        self._token_scraper = token_scraper
        self._token_lock = asyncio.Lock()

    async def __aenter__(self) -> AppleMusicClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    @property
    def storefront(self) -> str:
        return self.credentials.storefront

    @property
    def has_user_token(self) -> bool:
        return bool(self.credentials.media_user_token)

    # ------------------------------------------------------------------ tokens
    async def developer_token(self, force_refresh: bool = False) -> str:
        async with self._token_lock:
            if not force_refresh and self.credentials.developer_token_valid():
                return self.credentials.developer_token  # type: ignore[return-value]
            token = await self._token_scraper(self._http)
            self.credentials.developer_token = token
            if self.persist:
                save_credentials(self.credentials)
            return token

    def _headers(self, dev_token: str, user: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {dev_token}",
            "Origin": "https://music.apple.com",
            "Referer": "https://music.apple.com/",
            "Accept": "application/json",
            "User-Agent": BROWSER_UA,
        }
        if user:
            if not self.credentials.media_user_token:
                raise AuthError(USER_TOKEN_HELP)
            headers["media-user-token"] = self.credentials.media_user_token
        return headers

    # ----------------------------------------------------------------- request
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        user: bool = False,
    ) -> Any:
        """Send a request to amp-api, retrying 429/5xx and refreshing a rejected dev token once."""
        refreshed = False
        attempt = 0
        while True:
            dev_token = await self.developer_token()
            resp = await self._http.request(
                method,
                AMP_BASE + path,
                params=params,
                json=json,
                headers=self._headers(dev_token, user),
            )
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                attempt += 1
                await self._sleep(_retry_delay(resp, attempt))
                continue
            if resp.status_code == 401 and not refreshed:
                # The web-player developer token rotates; scrape a fresh one and retry once.
                refreshed = True
                await self.developer_token(force_refresh=True)
                continue
            if resp.status_code in (401, 403):
                if user:
                    raise AuthError(USER_TOKEN_HELP, resp.status_code, _safe_json(resp))
                raise AuthError(
                    "Apple Music rejected the developer token. Re-run `apple-music-mcp login`.",
                    resp.status_code,
                    _safe_json(resp),
                )
            if resp.status_code >= 400:
                raise AppleMusicError(
                    f"Apple Music API {method} {path} failed with HTTP {resp.status_code}",
                    resp.status_code,
                    _safe_json(resp),
                )
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

    # ----------------------------------------------------------------- catalog
    async def search_songs(self, term: str, limit: int = 10) -> list[Song]:
        """Search the catalog; falls back to the public iTunes Search API if amp-api is unusable."""
        limit = max(1, min(limit, MAX_SEARCH_LIMIT))
        try:
            data = await self.request(
                "GET",
                f"/v1/catalog/{self.storefront}/search",
                params={"term": term, "types": "songs", "limit": limit},
            )
        except (TokenScrapeError, AuthError, AppleMusicError, httpx.HTTPError):
            return await self.itunes_search_songs(term, limit)
        items = (((data or {}).get("results") or {}).get("songs") or {}).get("data") or []
        return [Song.from_amp(item) for item in items]

    async def itunes_search_songs(self, term: str, limit: int = 10) -> list[Song]:
        params = {
            "term": term,
            "entity": "song",
            "media": "music",
            "limit": limit,
            "country": self.storefront,
        }
        attempt = 0
        while True:
            resp = await self._http.get(ITUNES_SEARCH, params=params, headers={"User-Agent": BROWSER_UA})
            if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                attempt += 1
                await self._sleep(_retry_delay(resp, attempt))
                continue
            break
        if resp.status_code >= 400:
            raise AppleMusicError(f"iTunes Search failed with HTTP {resp.status_code}", resp.status_code)
        results = _safe_json(resp) or {}
        return [
            Song.from_itunes(r)
            for r in results.get("results", [])
            if r.get("wrapperType") == "track" and r.get("kind") == "song"
        ]

    async def search_artists(self, term: str, limit: int = 5) -> list[Artist]:
        data = await self.request(
            "GET",
            f"/v1/catalog/{self.storefront}/search",
            params={"term": term, "types": "artists", "limit": max(1, min(limit, MAX_SEARCH_LIMIT))},
        )
        items = (((data or {}).get("results") or {}).get("artists") or {}).get("data") or []
        return [Artist.from_amp(item) for item in items]

    async def artist_top_songs(self, artist_id: str, limit: int = 10) -> list[Song]:
        data = await self.request(
            "GET",
            f"/v1/catalog/{self.storefront}/artists/{artist_id}/view/top-songs",
            params={"limit": max(1, min(limit, 20))},
        )
        return [Song.from_amp(item) for item in (data or {}).get("data", [])][:limit]

    async def get_songs(self, ids: list[str]) -> list[Song]:
        songs: list[Song] = []
        for i in range(0, len(ids), 300):
            chunk = ids[i : i + 300]
            data = await self.request(
                "GET", f"/v1/catalog/{self.storefront}/songs", params={"ids": ",".join(chunk)}
            )
            songs.extend(Song.from_amp(item) for item in (data or {}).get("data", []))
        return songs

    # ----------------------------------------------------------------- account
    async def user_storefront(self) -> str:
        data = await self.request("GET", "/v1/me/storefront", user=True)
        items = (data or {}).get("data") or []
        if not items:
            raise AppleMusicError("Apple Music returned no storefront for this account")
        return str(items[0].get("id"))


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), 10.0)
        except ValueError:
            pass
    return min(10.0, random.uniform(0, 0.5 * 2**attempt))


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:500]
