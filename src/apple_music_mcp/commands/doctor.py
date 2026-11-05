"""`apple-music-mcp doctor`: live, step-by-step checks against the real Apple endpoints.

Checks are small objects in a list (open for extension: add a check without touching the runner).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .. import __version__
from ..apple.errors import AppleMusicError, AuthError
from ..config import CredentialStore
from ..container import AppContainer
from . import ContainerBuilder, Output, default_container

TEST_PLAYLIST = "apple-music-mcp doctor test"


@dataclass
class Check:
    name: str
    run: Callable[[AppContainer], Awaitable[str]]
    required: bool = True
    needs_login: bool = False
    writes: bool = False
    proves_login: bool = False  # success means the user token works


async def _developer_token(app: AppContainer) -> str:
    token = await app.tokens.get()
    exp = app.credentials.developer_token_expires_at
    days = (exp - time.time()) / 86400 if exp else 0
    return f"developer token valid for {days:.0f} more days ({len(token)} chars)"


async def _catalog(app: AppContainer) -> str:
    songs = await app.catalog.search_songs("Charlotte de Witte Doppler", limit=3)
    if not songs:
        raise AppleMusicError("catalog search returned no songs")
    return f"{songs[0].label} [{songs[0].id}] via {songs[0].source}"


async def _itunes(app: AppContainer) -> str:
    songs = await app.itunes.search_songs("Amelie Lens Hypnotized", limit=3)
    if not songs:
        raise AppleMusicError("iTunes Search returned no songs")
    return f"{songs[0].label} [{songs[0].id}]"


async def _account(app: AppContainer) -> str:
    if not app.has_user_token:
        raise AuthError("no media-user-token stored. Run: apple-music-mcp login")
    return f"account storefront '{await app.library.storefront()}'"


async def _library(app: AppContainer) -> str:
    return f"{len(await app.library.list_playlists(limit=200))} playlists in library"


async def _write_roundtrip(app: AppContainer) -> str:
    songs = await app.catalog.search_songs("Stephan Bodzin Singularity", limit=2)
    if not songs:
        raise AppleMusicError("no song to add")
    pl = await app.library.create_playlist(TEST_PLAYLIST, "Temporary, safe to delete")
    await app.library.add_tracks(pl.id, [s.id for s in songs])
    entries = await app.library.playlist_tracks(pl.id)
    try:
        await app.library.delete_playlist(pl.id)
        cleanup = "deleted again"
    except AppleMusicError:
        cleanup = f"could not delete it via API; delete '{TEST_PLAYLIST}' in the Music app"
    return f"created {pl.id} with {len(entries)} track(s), {cleanup}"


DEFAULT_CHECKS: list[Check] = [
    Check("developer token", _developer_token),
    Check("catalog search", _catalog),
    Check("iTunes Search fallback", _itunes, required=False),
    Check("user token", _account, proves_login=True),
    Check("library access", _library, needs_login=True),
    Check("create playlist (write test)", _write_roundtrip, needs_login=True, writes=True),
]


async def run_doctor(
    write: bool,
    *,
    build: ContainerBuilder = default_container,
    store: CredentialStore | None = None,
    checks: list[Check] | None = None,
    out: Output = print,
) -> int:
    store = store or CredentialStore()
    app = build(store.load())
    failures = 0
    logged_in = False
    out(f"apple-music-mcp {__version__} doctor  (credentials: {store.path})")
    try:
        for check in checks if checks is not None else DEFAULT_CHECKS:
            if (check.writes and not write) or (check.needs_login and not logged_in):
                continue
            try:
                detail = await check.run(app)
            except Exception as exc:  # noqa: BLE001 - diagnostics
                failures += int(check.required)
                tag = "FAIL" if check.required else "warn"
                out(f"  [{tag}] {check.name}: {exc.__class__.__name__}: {exc}")
                continue
            out(f"  [ok]   {check.name}: {detail}")
            logged_in = logged_in or check.proves_login
    finally:
        await app.aclose()
    out("All checks passed." if failures == 0 else f"{failures} check(s) failed.")
    return 0 if failures == 0 else 1
