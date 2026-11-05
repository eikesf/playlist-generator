"""Composition root: builds the object graph once. Everything else receives its dependencies."""

from __future__ import annotations

import asyncio

import httpx

from .apple.catalog import CatalogService
from .apple.http import RetryPolicy, Sleep
from .apple.itunes import ITunesSearch
from .apple.library import LibraryService
from .apple.taste import TasteService
from .apple.tokens import DeveloperTokenProvider, TokenScraper, scrape_developer_token
from .apple.transport import AmpTransport
from .config import Credentials, CredentialStore, StateStore
from .discovery import DiscoveryService
from .editing import PlaylistEditor


class AppContainer:
    def __init__(
        self,
        credentials: Credentials | None = None,
        *,
        store: CredentialStore | None = None,
        state: StateStore | None = None,
        http: httpx.AsyncClient | None = None,
        persist: bool = True,
        retry: RetryPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
        token_scraper: TokenScraper = scrape_developer_token,
    ):
        self.store = store or CredentialStore()
        self.state = state or StateStore()
        self.credentials = credentials or self.store.load()
        self._http = http or httpx.AsyncClient(timeout=30, follow_redirects=True)
        self._owns_http = http is None
        self.tokens = DeveloperTokenProvider(
            self.credentials, self._http, store=self.store if persist else None, scraper=token_scraper
        )
        self.transport = AmpTransport(self._http, self.credentials, self.tokens, retry, sleep)
        self.itunes = ITunesSearch(self._http, self.storefront, retry, sleep)
        self.catalog = CatalogService(self.transport, self.storefront, fallback=self.itunes)
        self.library = LibraryService(self.transport)
        self.editor = PlaylistEditor(self.library, self.state)
        self.taste = TasteService(self.transport, self.state)
        self.discovery = DiscoveryService(self.catalog, self.taste)

    def storefront(self) -> str:
        return self.credentials.storefront

    @property
    def has_user_token(self) -> bool:
        return bool(self.credentials.media_user_token)

    def set_persist(self, persist: bool) -> None:
        self.tokens.store = self.store if persist else None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> AppContainer:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
