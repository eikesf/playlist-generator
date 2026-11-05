"""`apple-music-mcp finish-pending`: create playlists saved while the login was expired."""

from __future__ import annotations

from ..apple.errors import AppleMusicError
from ..config import CredentialStore
from ..operations import PendingPlaylists
from . import ContainerBuilder, Output, default_container


async def run_finish_pending(
    *, build: ContainerBuilder = default_container, store: CredentialStore | None = None, out: Output = print
) -> int:
    app = build((store or CredentialStore()).load())
    try:
        pending = PendingPlaylists(app)
        if not pending.list():
            out("No pending playlists.")
            return 0
        for result in await pending.finish():
            pl = result["playlist"]
            out(f"Created '{pl['name']}' with {result['tracks_added']} tracks: {pl['url']}")
        return 0
    except AppleMusicError as exc:
        out(f"Could not create the pending playlists: {exc}")
        return 1
    finally:
        await app.aclose()
