"""`apple-music-mcp refresh`: a read-only refresh proposal, decided later in a conversation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..apple.errors import AppleMusicError
from ..config import CredentialStore
from ..refresh import RefreshService, RefreshSettings
from . import ContainerBuilder, Output, default_container

LABEL = "com.eikesf.apple-music-mcp.refresh"


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


async def run_refresh(
    playlist_name: str,
    *,
    notify: bool = False,
    build: ContainerBuilder = default_container,
    store: CredentialStore | None = None,
    out: Output = print,
) -> int:
    store = store or CredentialStore()
    app = build(store.load())
    try:
        target = next(
            (p for p in await app.library.list_playlists(limit=500) if p.name == playlist_name), None
        )
        if not target:
            out(f"Playlist '{playlist_name}' not found in your library.")
            return 1
        plan = await RefreshService(app, app.music_app).build_plan(target.id, RefreshSettings.from_app(app))
    except AppleMusicError as exc:
        out(f"Refresh failed: {exc}")
        return 1
    finally:
        await app.aclose()
    counts = plan["summary"]
    summary = (
        f"Proposta #{plan['proposal_id']} para '{playlist_name}': {counts['remove']} saem, "
        f"{counts['add']} entram. Nada foi removido: decida numa conversa com o Claude."
    )
    out(summary)
    out(json.dumps(plan, ensure_ascii=False, indent=2))
    if notify:
        _notify("Playlist refresh", summary)
    return 0


def _notify(title: str, message: str) -> None:
    if sys.platform != "darwin":
        return
    import subprocess

    safe = message.replace('"', "'")
    subprocess.run(
        ["osascript", "-e", f'display notification "{safe}" with title "{title}"'],
        check=False,
        capture_output=True,
    )


def retire_schedule_refresh(playlist_name: str, path: Path | None = None, out: Output = print) -> int:
    """`schedule-refresh` is replaced by the `refresh_plan` job. Remove the old weekly agent."""
    path = path or launch_agent_path()
    if path.exists():
        if sys.platform == "darwin":
            import os
            import subprocess

            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], check=False, capture_output=True
            )
        path.unlink()
        out(f"Old weekly refresh agent removed ({path.name}).")
    out(
        "`schedule-refresh` is retired. The weekly refresh is now a proposal you approve in Claude:\n"
        f"  1. In a conversation: turn the refresh on for '{playlist_name}' "
        "(update_managed_playlist refresh_enabled=true; adopt_playlist first if it is not managed).\n"
        "  2. On the Mac: apple-music-mcp schedule install  (adds the refresh_plan job, Monday 09:00 "
        "by default; REFRESH_WEEKDAY / REFRESH_TIME in .env)."
    )
    return 0
