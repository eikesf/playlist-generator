"""amp-api transport: headers, auth, retries, dev-token rotation and pagination."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import Credentials
from .errors import DEV_TOKEN_HELP, USER_TOKEN_HELP, AppleMusicError, AuthError
from .http import BROWSER_UA, RetryPolicy, Sleep, safe_json, send_with_retry
from .tokens import DeveloperTokenProvider

AMP_BASE = "https://amp-api.music.apple.com"


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
    ):
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
        refreshed = False
        while True:
            dev_token = await self._tokens.get()
            headers = self._headers(dev_token, user)

            async def send(headers: dict[str, str] = headers) -> httpx.Response:
                return await self._http.request(
                    method, AMP_BASE + path, params=params, json=json, headers=headers
                )

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
