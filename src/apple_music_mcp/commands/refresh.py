"""`apple-music-mcp refresh`: a read-only refresh proposal, decided later in a conversation."""

from __future__ import annotations

import json
import plistlib
import sys
from pathlib import Path

from ..apple.errors import AppleMusicError
from ..config import CredentialStore
from ..refresh import RefreshService, RefreshSettings
from . import ContainerBuilder, Output, default_container

LABEL = "com.eikesf.apple-music-mcp.refresh"


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "apple-music-mcp" / "refresh.log"


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


def schedule_refresh(
    playlist_name: str,
    weekday: int = 1,
    hour: int = 10,
    minute: int = 7,
    executable: str | None = None,
    path: Path | None = None,
) -> Path:
    """Write a launchd agent that runs the read-only refresh weekly (weekday: 0=Sunday ... 6)."""
    from .install import server_command

    exe = executable or server_command()[0]
    path = path or launch_agent_path()
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    program = [exe, "refresh", "--playlist", playlist_name, "--notify"]
    if exe.endswith("python") or exe.endswith("python3"):
        program = [exe, "-m", "apple_music_mcp", "refresh", "--playlist", playlist_name, "--notify"]
    plist = {
        "Label": LABEL,
        "ProgramArguments": program,
        "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "RunAtLoad": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump(plist, fh)
    return path
