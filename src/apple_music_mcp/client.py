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
from .models import Artist, Playlist, Song
from .tokens import BROWSER_UA, TokenScrapeError, scrape_developer_token

AMP_BASE = "https://amp-api.music.apple.com"
ITUNES_SEARCH = "https://itunes.apple.com/search"
MAX_SEARCH_LIMIT = 25
RETRY_STATUSES = {429, 500, 502, 503, 504}
ADD_TRACKS_CHUNK = 100


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

    # ----------------------------------------------------------------- library
    async def list_playlists(self, limit: int = 100) -> list[Playlist]:
        playlists: list[Playlist] = []
        path: str | None = "/v1/me/library/playlists"
        params: dict[str, Any] | None = {"limit": 100}
        while path and len(playlists) < limit:
            data = await self.request("GET", path, params=params, user=True) or {}
            playlists.extend(Playlist.from_amp(item) for item in data.get("data", []))
            path, params = data.get("next"), None
        return playlists[:limit]

    async def create_playlist(self, name: str, description: str | None = None) -> Playlist:
        attributes: dict[str, Any] = {"name": name}
        if description:
            attributes["description"] = description
        data = await self.request(
            "POST",
            "/v1/me/library/playlists",
            json={"attributes": attributes, "relationships": {"tracks": {"data": []}}},
            user=True,
        )
        items = (data or {}).get("data") or []
        if not items:
            raise AppleMusicError("Apple Music did not return the created playlist")
        return Playlist.from_amp(items[0])

    async def add_tracks(self, playlist_id: str, song_ids: list[str]) -> int:
        """Append catalog songs in order, in chunks. Returns how many were sent."""
        sent = 0
        for i in range(0, len(song_ids), ADD_TRACKS_CHUNK):
            chunk = song_ids[i : i + ADD_TRACKS_CHUNK]
            await self.request(
                "POST",
                f"/v1/me/library/playlists/{playlist_id}/tracks",
                json={"data": [{"id": sid, "type": "songs"} for sid in chunk]},
                user=True,
            )
            sent += len(chunk)
        return sent

    async def playlist_tracks(self, playlist_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        path: str | None = f"/v1/me/library/playlists/{playlist_id}/tracks"
        params: dict[str, Any] | None = {"limit": 100}
        while path:
            data = await self.request("GET", path, params=params, user=True) or {}
            for item in data.get("data", []):
                attrs = item.get("attributes") or {}
                play = attrs.get("playParams") or {}
                items.append(
                    {
                        "library_id": item.get("id"),
                        "catalog_id": play.get("catalogId") or play.get("id"),
                        "name": attrs.get("name"),
                        "artist": attrs.get("artistName"),
                    }
                )
            path, params = data.get("next"), None
        return items

    async def delete_playlist(self, playlist_id: str) -> None:
        await self.request("DELETE", f"/v1/me/library/playlists/{playlist_id}", user=True)


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
