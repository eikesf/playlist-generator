"""``apple-music-mcp`` command line.

Subcommands:
  serve            run the MCP server over stdio (what Claude Desktop / Claude Code launch)
  login            store your music.apple.com session token (paste it, or --browser to capture it)
  status           show stored credentials (masked)
  doctor           live end-to-end checks against Apple Music (--write also creates a test playlist)
  install-claude   register the server in Claude Desktop (and print the Claude Code command)
  set-bpm-key      store a free GetSongBPM API key (better BPM data; optional)
  run              run one scheduled job now: doctor, snapshot, recent_played, backup, heartbeat
  schedule         install / uninstall the launchd jobs that run them on this Mac
  pause / resume   pause mode for trips: jobs skip and alerts stay quiet
  finish-pending   create playlists saved while the login was expired
  refresh          read-only weekly refresh plan: fills "<name> · Descobertas", lists candidates
  schedule-refresh run `refresh` weekly on this Mac (launchd); nothing is ever removed automatically
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from . import __version__
from .config import CredentialStore
from .logging_setup import setup_logging
from .settings import load_env_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apple-music-mcp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", action="version", version=f"apple-music-mcp {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server (stdio)")
    login = sub.add_parser("login", help="store your Apple Music session token")
    login.add_argument("--token", help="media-user-token value (otherwise prompted, hidden)")
    login.add_argument("--storefront", help="two-letter storefront, default: your account's")
    login.add_argument("--browser", action="store_true", help="open a browser window and capture tokens")
    login.add_argument("--expires", help="cookie expiry date YYYY-MM-DD (otherwise asked; empty = estimate)")
    sub.add_parser("status", help="show stored credentials (masked)")
    doctor = sub.add_parser("doctor", help="live checks against Apple Music")
    doctor.add_argument("--write", action="store_true", help="also create (and delete) a test playlist")
    sub.add_parser("install-claude", help="register the MCP server in Claude Desktop / Claude Code")
    refresh = sub.add_parser("refresh", help="read-only refresh plan + discovery staging playlist")
    refresh.add_argument("--playlist", required=True, help="exact playlist name in your library")
    refresh.add_argument("--new-tracks", type=int, help="how many discoveries to stage (default: auto)")
    refresh.add_argument("--notify", action="store_true", help="show a macOS notification with the summary")
    sched = sub.add_parser("schedule-refresh", help="run `refresh` weekly via launchd (macOS)")
    sched.add_argument("--playlist", required=True)
    sched.add_argument("--weekday", type=int, default=1, help="0=Sunday ... 6=Saturday (default Monday)")
    sched.add_argument("--hour", type=int, default=10)
    sched.add_argument("--minute", type=int, default=7)
    run = sub.add_parser("run", help="run one scheduled job now (what launchd calls)")
    run.add_argument("job", choices=["doctor", "backup", "snapshot", "recent_played", "heartbeat"])
    pause = sub.add_parser("pause", help="pause scheduled jobs and alerts (trips); see `resume`")
    pause.add_argument("--until", help="YYYY-MM-DD: resume automatically after this day")
    pause.add_argument("--reason")
    sub.add_parser("resume", help="end pause mode")
    schedule = sub.add_parser("schedule", help="install/remove the launchd jobs (doctor, snapshot, ...)")
    schedule.add_argument("action", choices=["install", "uninstall"])
    sub.add_parser("finish-pending", help="create playlists that were saved while the login was expired")
    bpm = sub.add_parser("set-bpm-key", help="store a GetSongBPM API key (getsongbpm.com/api)")
    bpm.add_argument("--key", help="the key (otherwise prompted, hidden); empty string removes it")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_env_file()
    setup_logging()
    if args.command is None and sys.stdin.isatty():
        parser.print_help()
        return 0
    if args.command in (None, "serve"):
        from .server import main as serve

        serve()
        return 0
    if args.command == "login":
        from .commands.login import run_login

        return asyncio.run(run_login(args.token, args.storefront, args.browser, expires=args.expires))
    if args.command == "status":
        from .commands.status import run_status

        return run_status()
    if args.command == "doctor":
        from .commands.doctor import run_doctor

        return asyncio.run(run_doctor(args.write))
    if args.command == "refresh":
        from .commands.refresh import run_refresh

        return asyncio.run(run_refresh(args.playlist, new_tracks=args.new_tracks, notify=args.notify))
    if args.command == "schedule-refresh":
        from .commands.refresh import schedule_refresh

        path = schedule_refresh(args.playlist, args.weekday, args.hour, args.minute)
        print(f"Weekly refresh written to {path}")
        print(f"Activate it with:  launchctl load -w {path}")
        print(f"Stop it with:      launchctl unload -w {path} && rm {path}")
        return 0
    if args.command == "run":
        from .commands.jobs import run_job_command

        return asyncio.run(run_job_command(args.job))
    if args.command in ("pause", "resume"):
        from .commands.pause import run_pause, run_resume

        if args.command == "pause":
            return asyncio.run(run_pause(args.until, args.reason))
        return asyncio.run(run_resume())
    if args.command == "schedule":
        from .commands.jobs import install_schedule, uninstall_schedule

        return install_schedule() if args.action == "install" else uninstall_schedule()
    if args.command == "finish-pending":
        from .commands.pending import run_finish_pending

        return asyncio.run(run_finish_pending())
    if args.command == "set-bpm-key":
        import getpass

        store = CredentialStore()
        creds = store.load()
        key = args.key if args.key is not None else getpass.getpass("GetSongBPM API key (hidden): ")
        creds.getsongbpm_api_key = key.strip() or None
        store.save(creds)
        print("GetSongBPM key " + ("saved." if creds.getsongbpm_api_key else "removed."))
        return 0
    if args.command == "install-claude":
        from .commands.install import run_install_claude

        return run_install_claude()
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
