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
from ..local_music import PlaylistNotInMusicApp
from . import ContainerBuilder, Output, default_container

TEST_PLAYLIST = "apple-music-mcp doctor test"
MUSIC_APP_ATTEMPTS = 5  # local osascript calls only, no network


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
    songs = await app.catalog.search_songs("Stephan Bodzin Singularity", limit=3)
    if len(songs) < 2:
        raise AppleMusicError("need at least 2 songs for the write test")
    ids = [s.id for s in songs[:2]]
    pl = await app.library.create_playlist(TEST_PLAYLIST, "Temporary, safe to delete")
    try:
        await app.library.add_tracks(pl.id, ids)
        entries = await app.library.playlist_tracks(pl.id)
        edit = await app.editor.sync(pl.id, list(reversed(ids)))
        pl_id = edit.playlist.id
        detail = (
            f"created with {len(entries)} track(s); reorder via '{edit.strategy}' "
            f"{'verified' if edit.verified else 'NOT verified'}; "
            f"removal request shape: {app.library.remove_variant or 'not needed'}"
        )
    except Exception:
        await _cleanup(app, pl.id)
        raise
    return f"{detail}; {await _cleanup(app, pl_id)}"


async def _cleanup(app: AppContainer, playlist_id: str) -> str:
    try:
        await app.library.delete_playlist(playlist_id)
        return "test playlist deleted"
    except AppleMusicError:
        return f"could not delete it via API; delete '{TEST_PLAYLIST}' in the Music app"


async def _bpm(app: AppContainer) -> str:
    if not app.bpm.providers:
        raise AppleMusicError("no BPM provider enabled")
    parts = []
    for provider in app.bpm.providers:
        match = await provider.lookup("Eric Prydz", "Opus")  # analysed in Deezer (126 BPM)
        parts.append(f"{provider.name}: {match.bpm:.0f} BPM" if match else f"{provider.name}: not found")
    if all("not found" in p for p in parts):
        raise AppleMusicError("; ".join(parts) + " (Claude's estimates will be used)")
    return "; ".join(parts)


async def _recent_played(app: AppContainer) -> str:
    songs, other = await app.taste.recent_tracks()
    return (
        f"{len(songs)} recent songs (+{other} non-song items such as stations); "
        f"latest: {songs[0].label if songs else 'none'}"
    )


async def _ratings(app: AppContainer) -> str:
    page = await app.transport.request("GET", "/v1/me/library/songs", params={"limit": 25}, user=True) or {}
    ids = [
        str((i.get("attributes") or {}).get("playParams", {}).get("catalogId"))
        for i in page.get("data", [])
        if (i.get("attributes") or {}).get("playParams", {}).get("catalogId")
    ]
    if not ids:
        return "no catalog songs in the first library page to check"
    ratings = await app.taste.ratings(ids)
    loved = sum(1 for v in ratings.values() if v == 1)
    disliked = sum(1 for v in ratings.values() if v == -1)
    return f"checked {len(ids)} library songs: {loved} loved, {disliked} disliked"


async def _heavy_rotation(app: AppContainer) -> str:
    data = (
        await app.transport.request("GET", "/v1/me/history/heavy-rotation", params={"limit": 10}, user=True)
        or {}
    )
    items = data.get("data") or []
    artists = {str(name) for i in items if (name := (i.get("attributes") or {}).get("artistName"))}
    return f"{len(items)} items ({', '.join(sorted(artists)[:3]) or 'no artists'})"


async def _similar_artists(app: AppContainer) -> str:
    artist = await app.catalog.find_artist("Charlotte de Witte")
    if not artist:
        raise AppleMusicError("artist lookup failed")
    similar = await app.catalog.similar_artists(artist.id, limit=5)
    if not similar:
        raise AppleMusicError("no similar artists returned (view may not exist)")
    return ", ".join(a.name for a in similar)


async def _music_app_stats(app: AppContainer) -> str:
    playlists = await app.library.list_playlists(limit=20)
    # Playlists the owner created are the ones refresh manages; catalog playlists added to the
    # library (can_edit false) are not reachable through AppleScript.
    own = [p for p in playlists if p.can_edit is not False]
    if not own:
        raise AppleMusicError("no playlist created by you in the library to read stats from")
    skipped = 0
    for playlist in own[:MUSIC_APP_ATTEMPTS]:
        try:
            stats = await app.music_app.playlist_stats(playlist.name)
        except PlaylistNotInMusicApp:
            skipped += 1
            continue
        played = sum(1 for s in stats if s.played)
        favorites = sum(1 for s in stats if s.favorited)
        note = f" ({skipped} playlist(s) not in the Music app skipped)" if skipped else ""
        pids = "yes" if stats and all(st.persistent_id for st in stats) else "no"
        return (
            f"'{playlist.name}': {len(stats)} tracks, {played} with plays, {favorites} favorites, "
            f"persistent IDs: {pids}{note}"
        )
    raise AppleMusicError(f"none of the first {skipped} library playlists was found in the Music app")


DEFAULT_CHECKS: list[Check] = [
    Check("developer token", _developer_token),
    Check("catalog search", _catalog),
    Check("iTunes Search fallback", _itunes, required=False),
    Check("BPM databases", _bpm, required=False),
    Check("user token", _account, proves_login=True),
    Check("library access", _library, needs_login=True),
    Check("recently played (unverified endpoint)", _recent_played, required=False, needs_login=True),
    Check("song ratings (unverified endpoint)", _ratings, required=False, needs_login=True),
    Check("heavy rotation (unverified endpoint)", _heavy_rotation, required=False, needs_login=True),
    Check("similar artists (unverified endpoint)", _similar_artists, required=False),
    Check("Music app stats via AppleScript (macOS)", _music_app_stats, required=False, needs_login=True),
    Check("create + reorder playlist (write test)", _write_roundtrip, needs_login=True, writes=True),
]


async def run_checks(app: AppContainer, checks: list[Check]) -> list[dict[str, str]]:
    """Run checks in order. Login-dependent checks run only after a check proved the login works.

    Each result is ``{"name", "status": ok|warn|FAIL|skipped, "detail"}``; messages never hold tokens.
    """
    results: list[dict[str, str]] = []
    logged_in = False
    for check in checks:
        if check.needs_login and not logged_in:
            results.append({"name": check.name, "status": "skipped", "detail": "needs a working login"})
            continue
        try:
            detail = await check.run(app)
        except Exception as exc:  # noqa: BLE001 - diagnostics
            status = "FAIL" if check.required else "warn"
            results.append(
                {"name": check.name, "status": status, "detail": f"{exc.__class__.__name__}: {exc}"}
            )
            continue
        results.append({"name": check.name, "status": "ok", "detail": detail})
        logged_in = logged_in or check.proves_login
    return results


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
    out(f"apple-music-mcp {__version__} doctor  (credentials: {store.location})")
    selected = [c for c in (checks if checks is not None else DEFAULT_CHECKS) if write or not c.writes]
    try:
        results = await run_checks(app, selected)
    finally:
        await app.aclose()
    for r in results:
        if r["status"] == "skipped":
            continue
        tag = {"ok": "ok]  ", "warn": "warn]", "FAIL": "FAIL]"}[r["status"]]
        out(f"  [{tag} {r['name']}: {r['detail']}")
    failures = sum(1 for r in results if r["status"] == "FAIL")
    out("All checks passed." if failures == 0 else f"{failures} check(s) failed.")
    return 0 if failures == 0 else 1
