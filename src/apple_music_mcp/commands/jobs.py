"""`apple-music-mcp run <job>` and `apple-music-mcp schedule install|uninstall|status` (launchd)."""

from __future__ import annotations

import os
import platform
import plistlib
import subprocess
from pathlib import Path
from typing import Any

from ..jobs import JOB_NAMES, Notifier, run_job
from ..settings import AppSettings
from . import ContainerBuilder, Output, default_container

LABEL_PREFIX = "com.eikesf.apple-music-mcp."


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def logs_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "apple-music-mcp"


def _calendar(times: tuple[str, ...] | list[str]) -> list[dict[str, int]]:
    out = []
    for t in times:
        hh, mm = t.split(":")
        out.append({"Hour": int(hh), "Minute": int(mm)})
    return out


def job_schedules(settings: AppSettings) -> dict[str, dict[str, Any]]:
    """launchd timing per job. Calendar jobs missed while the Mac sleeps run when it wakes."""
    return {
        "doctor": {"StartCalendarInterval": _calendar([settings.doctor_time])},
        "snapshot": {"StartCalendarInterval": _calendar([settings.snapshot_time])},
        "recent_played": {"StartCalendarInterval": _calendar(settings.recent_played_times)},
        "backup": {"StartCalendarInterval": _calendar([settings.backup_time])},
        "heartbeat": {"StartInterval": settings.heartbeat_minutes * 60},
    }


def program_for(job: str, executable: str | None = None) -> list[str]:
    from .install import server_command

    exe = executable or server_command()[0]
    if exe.endswith(("python", "python3")):
        return [exe, "-m", "apple_music_mcp", "run", job]
    return [exe, "run", job]


def write_agents(
    settings: AppSettings,
    *,
    executable: str | None = None,
    directory: Path | None = None,
    logs: Path | None = None,
) -> list[Path]:
    directory = directory or agents_dir()
    logs = logs or logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    paths = []
    for job, timing in job_schedules(settings).items():
        label = LABEL_PREFIX + job
        log = str(logs / f"{job}.log")
        plist = {
            "Label": label,
            "ProgramArguments": program_for(job, executable),
            **timing,
            "StandardOutPath": log,
            "StandardErrorPath": log,
            "RunAtLoad": False,
            "ProcessType": "Background",
            # launchd starts jobs with a minimal environment; keep the user's config directory.
            "EnvironmentVariables": {
                k: v for k, v in os.environ.items() if k in ("APPLE_MUSIC_MCP_CONFIG_DIR", "XDG_CONFIG_HOME")
            },
        }
        path = directory / f"{label}.plist"
        with open(path, "wb") as fh:
            plistlib.dump(plist, fh)
        paths.append(path)
    return paths


def _launchctl(*args: str) -> None:
    subprocess.run(["launchctl", *args], check=False, capture_output=True)


def install_schedule(out: Output = print) -> int:
    settings = AppSettings.from_env()
    paths = write_agents(settings)
    on_mac = platform.system() == "Darwin"
    for path in paths:
        if on_mac:
            _launchctl("bootout", f"gui/{os.getuid()}", str(path))
            _launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
        out(f"  {path.name}")
    out(f"Scheduled {len(paths)} jobs (logs in {logs_dir()}):")
    for job, timing in job_schedules(settings).items():
        when = (
            f"every {timing['StartInterval'] // 60} min"
            if "StartInterval" in timing
            else ", ".join(f"{c['Hour']:02d}:{c['Minute']:02d}" for c in timing["StartCalendarInterval"])
        )
        out(f"  {job:<14} {when}")
    if not on_mac:
        out("Not on macOS: files written but not loaded.")
    return 0


def uninstall_schedule(out: Output = print) -> int:
    removed = 0
    for job in JOB_NAMES:
        path = agents_dir() / f"{LABEL_PREFIX}{job}.plist"
        if path.exists():
            if platform.system() == "Darwin":
                _launchctl("bootout", f"gui/{os.getuid()}", str(path))
            path.unlink()
            removed += 1
    out(f"Removed {removed} scheduled job(s). Data in the database is kept.")
    return 0


async def run_job_command(
    job: str,
    *,
    build: ContainerBuilder = default_container,
    notifier: Notifier | None = None,
    out: Output = print,
) -> int:
    from ..config import CredentialStore

    app = build(CredentialStore().load())
    try:
        if notifier is None:
            from ..monitoring import default_notifier

            notifier = default_notifier(app.settings, app._http)
        outcome = await run_job(job, app, notifier)
    except Exception as exc:  # noqa: BLE001 - recorded in job_run, reported here
        out(f"{job}: FAILED {type(exc).__name__}: {exc}")
        return 1
    finally:
        await app.aclose()
    out(f"{job}: {outcome.status} {outcome.detail}")
    return 0 if outcome.status in ("ok", "warn", "skipped_paused") else 1
