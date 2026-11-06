"""Composition root: builds the object graph once. Everything else receives its dependencies."""

from __future__ import annotations

import asyncio

import httpx

from .apple.catalog import CatalogService
from .apple.http import RateLimiter, RetryPolicy, Sleep
from .apple.itunes import ITunesSearch
from .apple.library import LibraryService
from .apple.taste import TasteService
from .apple.tokens import DeveloperTokenProvider, TokenScraper, scrape_developer_token
from .apple.transport import AmpTransport
from .bpm.providers import BpmProvider, DeezerProvider, GetSongBpmProvider
from .bpm.service import BpmService
from .config import STATE_FILE, Credentials, CredentialStore, config_dir
from .curation.registry import ProfileRegistry
from .db import Database
from .db.cache import ResponseCache
from .db.repositories import ListeningRepository, ProfileRepository
from .db.settings import SettingsStore, import_state_file
from .discovery import DiscoveryService
from .editing import PlaylistEditor
from .feedback import Blacklist, BlacklistRepository, FeedbackRepository
from .local_music import MusicAppReader
from .ports import KeyValueStore
from .settings import AppSettings


class AppContainer:
    def __init__(
        self,
        credentials: Credentials | None = None,
        *,
        store: CredentialStore | None = None,
        state: KeyValueStore | None = None,
        db: Database | None = None,
        http: httpx.AsyncClient | None = None,
        persist: bool = True,
        retry: RetryPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
        token_scraper: TokenScraper = scrape_developer_token,
        use_deezer: bool = True,
        music_app: MusicAppReader | None = None,
        settings: AppSettings | None = None,
    ):
        self.settings = settings or AppSettings.from_env()
        self.store = store or CredentialStore()
        self.db: Database = db or Database.open()
        if state is None:
            kv = SettingsStore(self.db)
            import_state_file(kv, config_dir() / STATE_FILE)
            state = kv
        self.state: KeyValueStore = state
        self.credentials = credentials or self.store.load()
        self._http = http or httpx.AsyncClient(timeout=30, follow_redirects=True)
        self._owns_http = http is None
        self.tokens = DeveloperTokenProvider(
            self.credentials, self._http, store=self.store if persist else None, scraper=token_scraper
        )
        # One limiter per service, shared by every caller (guide §4.1: ~5 req/s, 3 in flight).
        self.apple_limiter = RateLimiter(rate=5, concurrency=3, sleep=sleep)
        self.cache = ResponseCache(self.db)
        self.profiles = ProfileRegistry(ProfileRepository(self.db))
        self.blacklist_entries = BlacklistRepository(self.db)
        self.feedback = FeedbackRepository(self.db)
        self.transport = AmpTransport(
            self._http,
            self.credentials,
            self.tokens,
            retry,
            sleep,
            enabled=self.settings.apple_web_api_enabled,
            limiter=self.apple_limiter,
            cache=self.cache,
        )
        self.itunes = ITunesSearch(self._http, self.storefront, retry, sleep, limiter=self.apple_limiter)
        self.catalog = CatalogService(self.transport, self.storefront, fallback=self.itunes)
        self.library = LibraryService(self.transport)
        self.editor = PlaylistEditor(self.library, self.state)
        self.taste = TasteService(self.transport, self.state)
        self.discovery = DiscoveryService(
            self.catalog,
            self.taste,
            ListeningRepository(self.db),
            min_plays=self.settings.known_min_plays,
            blacklist=self.blacklist,
        )
        providers: list[BpmProvider] = []
        if self.credentials.getsongbpm_api_key:
            providers.append(
                GetSongBpmProvider(self._http, self.credentials.getsongbpm_api_key, retry=retry, sleep=sleep)
            )
        if use_deezer:
            providers.append(DeezerProvider(self._http, retry=retry, sleep=sleep))
        self.bpm = BpmService(providers, self.state)
        self.music_app = music_app or MusicAppReader()

    def storefront(self) -> str:
        return self.credentials.storefront

    @property
    def has_user_token(self) -> bool:
        return bool(self.credentials.media_user_token)

    def set_persist(self, persist: bool) -> None:
        self.tokens.store = self.store if persist else None

    def blacklist(self) -> Blacklist:
        """The active blacklist, read fresh (it is small and edited from the chat)."""
        return Blacklist(self.blacklist_entries.active())

    @property
    def http(self) -> httpx.AsyncClient:
        """The shared HTTP client (for monitoring and other non-Apple calls)."""
        return self._http

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
        self.db.dispose()

    async def __aenter__(self) -> AppContainer:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
