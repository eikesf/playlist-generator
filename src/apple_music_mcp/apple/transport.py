"""amp-api transport: headers, auth, retries, dev-token rotation and pagination."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import httpx

from ..config import Credentials
from ..db.cache import cache_key
from .errors import DEV_TOKEN_HELP, USER_TOKEN_HELP, AppleMusicError, AuthError, WebApiDisabled
from .http import BROWSER_UA, RateLimiter, RetryPolicy, Sleep, safe_json, send_with_retry
from .tokens import DeveloperTokenProvider

AMP_BASE = "https://amp-api.music.apple.com"


@dataclass(frozen=True)
class CacheRule:
    namespace: str
    pattern: re.Pattern[str]
    ttl: timedelta


# What may be served from the cache (guide §4.1). Playlists, storefront and recently played are never
# cached: edits must always see Apple's current state. Any write under /v1/me/library empties "library".
CACHE_RULES = (
    CacheRule("catalog", re.compile(r"^/v1/catalog/[^/]+/(search|songs|artists)"), timedelta(days=7)),
    CacheRule("library", re.compile(r"^/v1/me/library/songs"), timedelta(hours=24)),
    CacheRule("ratings", re.compile(r"^/v1/me/ratings/"), timedelta(hours=24)),
)


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, payload: Any, ttl: timedelta) -> None: ...

    def invalidate(self, namespace: str) -> int: ...


def cache_rule(method: str, path: str) -> CacheRule | None:
    if method != "GET":
        return None
    return next((rule for rule in CACHE_RULES if rule.pattern.match(path)), None)


class AmpTransport:
    """Sends requests to amp-api. Catalog calls need only the developer token; ``user=True``
    calls (``/v1/me``) also send the ``media-user-token``."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        credentials: Credentials,
        tokens: DeveloperTokenProvider,
        retry: RetryPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
        enabled: bool = True,
        limiter: RateLimiter | None = None,
        cache: Cache | None = None,
    ):
        self.enabled = enabled
        self._limiter = limiter or RateLimiter(sleep=sleep)
        self._cache = cache
        self._http = http
        self._credentials = credentials
        self._tokens = tokens
        self._retry = retry or RetryPolicy()
        self._sleep = sleep

    def _headers(self, dev_token: str, user: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {dev_token}",
            "Origin": "https://music.apple.com",
            "Referer": "https://music.apple.com/",
            "Accept": "application/json",
            "User-Agent": BROWSER_UA,
        }
        if user:
            if not self._credentials.media_user_token:
                raise AuthError(USER_TOKEN_HELP)
            headers["media-user-token"] = self._credentials.media_user_token
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        user: bool = False,
    ) -> Any:
        if not self.enabled:
            raise WebApiDisabled()
        rule = cache_rule(method, path) if self._cache else None
        key = cache_key(rule.namespace, path, params) if rule else None
        if key and self._cache:
            hit = self._cache.get(key)
            if hit is not None:
                return hit
        result = await self._request(method, path, params=params, json=json, user=user)
        if key and rule and self._cache and result is not None:
            self._cache.set(key, result, rule.ttl)
        if method != "GET" and self._cache and path.startswith("/v1/me/library"):
            self._cache.invalidate("library")
        return result

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None, json: Any, user: bool
    ) -> Any:
        refreshed = False
        while True:
            dev_token = await self._tokens.get()
            headers = self._headers(dev_token, user)

            async def send(headers: dict[str, str] = headers) -> httpx.Response:
                return await self._http.request(
                    method, AMP_BASE + path, params=params, json=json, headers=headers
                )

            async with self._limiter:
                resp = await send_with_retry(send, self._retry, self._sleep)
            if resp.status_code == 401 and not refreshed:
                # The web-player developer token rotates: scrape a fresh one and retry once.
                refreshed = True
                await self._tokens.get(force_refresh=True)
                continue
            return self._parse(resp, method, path, user)

    @staticmethod
    def _parse(resp: httpx.Response, method: str, path: str, user: bool) -> Any:
        if resp.status_code in (401, 403):
            raise AuthError(USER_TOKEN_HELP if user else DEV_TOKEN_HELP, resp.status_code, safe_json(resp))
        if resp.status_code >= 400:
            raise AppleMusicError(
                f"Apple Music API {method} {path} failed with HTTP {resp.status_code}",
                resp.status_code,
                safe_json(resp),
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def paginate(
        self, path: str, *, params: dict[str, Any] | None = None, user: bool = False, limit: int | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield JSON:API resources across pages, following ``next`` links."""
        count = 0
        next_path: str | None = path
        next_params = params
        while next_path:
            data = await self.request("GET", next_path, params=next_params, user=user) or {}
            for item in data.get("data", []):
                yield item
                count += 1
                if limit is not None and count >= limit:
                    return
            next_path, next_params = data.get("next"), None
