"""``apple-music-mcp`` command line.

Subcommands:
  serve            run the MCP server over stdio (what Claude Desktop / Claude Code launch)
  login            store your music.apple.com session token (paste it, or --browser to capture it)
  status           show stored credentials (masked)
  doctor           live end-to-end checks against Apple Music (--write also creates a test playlist)
  install-claude   register the server in Claude Desktop (and print the Claude Code command)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import __version__
from .config import CredentialStore


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
    sub.add_parser("status", help="show stored credentials (masked)")
    doctor = sub.add_parser("doctor", help="live checks against Apple Music")
    doctor.add_argument("--write", action="store_true", help="also create (and delete) a test playlist")
    sub.add_parser("install-claude", help="register the MCP server in Claude Desktop / Claude Code")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None and sys.stdin.isatty():
        parser.print_help()
        return 0
    if args.command in (None, "serve"):
        from .server import main as serve

        serve()
        return 0
    if args.command == "login":
        from .commands.login import run_login

        return asyncio.run(run_login(args.token, args.storefront, args.browser))
    if args.command == "status":
        store = CredentialStore()
        print(json.dumps({"file": str(store.path), **store.load().redacted()}, indent=2))
        return 0
    if args.command == "doctor":
        from .commands.doctor import run_doctor

        return asyncio.run(run_doctor(args.write))
    if args.command == "install-claude":
        from .commands.install import run_install_claude

        return run_install_claude()
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
