"""Public iTunes Search API (no auth). Its ``trackId`` is the Apple Music catalog song id."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx

from ..models import Song
from .errors import AppleMusicError
from .http import BROWSER_UA, RetryPolicy, Sleep, safe_json, send_with_retry

ITUNES_SEARCH = "https://itunes.apple.com/search"


class ITunesSearch:
    def __init__(
        self,
        http: httpx.AsyncClient,
        storefront: Callable[[], str],
        retry: RetryPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
    ):
        self._http = http
        self._storefront = storefront
        self._retry = retry or RetryPolicy()
        self._sleep = sleep

    async def search_songs(self, term: str, limit: int = 10) -> list[Song]:
        params: dict[str, str | int] = {
            "term": term,
            "entity": "song",
            "media": "music",
            "limit": limit,
            "country": self._storefront(),
        }

        async def send() -> httpx.Response:
            return await self._http.get(ITUNES_SEARCH, params=params, headers={"User-Agent": BROWSER_UA})

        resp = await send_with_retry(send, self._retry, self._sleep)
        if resp.status_code >= 400:
            raise AppleMusicError(f"iTunes Search failed with HTTP {resp.status_code}", resp.status_code)
        results = safe_json(resp) or {}
        return [
            Song.from_itunes(r)
            for r in results.get("results", [])
            if r.get("wrapperType") == "track" and r.get("kind") == "song"
        ]
