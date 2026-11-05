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
import getpass
import json
import os
import platform
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import __version__
from .client import AppleMusicClient, AppleMusicError, AuthError
from .config import Credentials, config_dir, credentials_path, load_credentials, save_credentials
from .tokens import TokenScrapeError

TOKEN_HELP = """\
How to get your media-user-token (one time, about 2 minutes):

  Safari: Settings > Advanced > tick "Show features for web developers".
          Open https://music.apple.com and sign in.
          Develop menu > Show Web Inspector > Storage tab > Cookies > music.apple.com
          Copy the value of the cookie named  media-user-token

  Chrome: open https://music.apple.com and sign in, press Cmd+Option+I,
          Application tab > Cookies > https://music.apple.com > media-user-token

The token stays on this Mac (file permissions 600) and is only sent to Apple.
"""


# ----------------------------------------------------------------------------- login
async def do_login(
    token: str | None,
    storefront: str | None,
    use_browser: bool,
    client_factory: Callable[[Credentials], AppleMusicClient] | None = None,
    prompt: Callable[[str], str] = getpass.getpass,
    out: Callable[[str], None] = print,
) -> int:
    creds = load_credentials()
    if storefront:
        creds.storefront = storefront.lower()
    if use_browser:
        from .browser_login import capture_tokens

        captured = await capture_tokens(config_dir() / "browser-profile", creds.storefront)
        if not captured.media_user_token:
            out(
                "No media-user-token cookie seen. Did the sign-in finish? Retry, or paste the token manually."
            )
            return 1
        creds.media_user_token = captured.media_user_token
        if captured.developer_token:
            creds.developer_token = captured.developer_token
    else:
        if not token:
            out(TOKEN_HELP)
            token = prompt("Paste media-user-token (input hidden): ")
        token = (token or "").strip().strip('"')
        if len(token) < 20:
            out("That does not look like a media-user-token (too short).")
            return 1
        creds.media_user_token = token

    factory = client_factory or (lambda c: AppleMusicClient(c))
    client = factory(creds)
    client.persist = False  # save only after Apple accepted the token
    try:
        await client.developer_token()
        account_sf = await client.user_storefront()
    except (AuthError, TokenScrapeError, AppleMusicError) as exc:
        out(f"Login check failed: {exc}")
        return 1
    finally:
        await client.aclose()
    if not storefront and account_sf:
        creds.storefront = account_sf
    path = save_credentials(creds)
    out(f"OK. Signed in (storefront '{creds.storefront}'). Credentials saved to {path}")
    return 0


# ---------------------------------------------------------------------------- doctor
async def do_doctor(
    write: bool,
    client_factory: Callable[[Credentials], AppleMusicClient] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    creds = load_credentials()
    client = (client_factory or (lambda c: AppleMusicClient(c)))(creds)
    failures = 0

    async def step(name: str, fn: Callable[[], Awaitable[str]], required: bool = True) -> Any:
        nonlocal failures
        try:
            detail = await fn()
            out(f"  [ok]   {name}: {detail}")
            return True
        except Exception as exc:  # noqa: BLE001 - diagnostics
            failures += 1 if required else 0
            out(f"  [{'FAIL' if required else 'warn'}] {name}: {exc.__class__.__name__}: {exc}")
            return False

    out(f"apple-music-mcp {__version__} doctor  (credentials: {credentials_path()})")
    try:

        async def dev_token() -> str:
            token = await client.developer_token()
            exp = client.credentials.developer_token_expires_at
            days = (exp - time.time()) / 86400 if exp else 0
            return f"developer token valid for {days:.0f} more days ({len(token)} chars)"

        await step("developer token", dev_token)

        async def catalog() -> str:
            songs = await client.search_songs("Charlotte de Witte Doppler", limit=3)
            if not songs:
                raise AppleMusicError("catalog search returned no songs")
            return f"{songs[0].label} [{songs[0].id}] via {songs[0].source}"

        await step("catalog search", catalog)

        async def itunes() -> str:
            songs = await client.itunes_search_songs("Amelie Lens Hypnotized", limit=3)
            if not songs:
                raise AppleMusicError("iTunes Search returned no songs")
            return f"{songs[0].label} [{songs[0].id}]"

        await step("iTunes Search fallback", itunes, required=False)

        async def account() -> str:
            if not client.has_user_token:
                raise AuthError("no media-user-token stored. Run: apple-music-mcp login")
            return f"account storefront '{await client.user_storefront()}'"

        logged_in = await step("user token", account)

        async def playlists() -> str:
            return f"{len(await client.list_playlists(limit=200))} playlists in library"

        if logged_in:
            await step("library access", playlists)

        if write and logged_in:

            async def roundtrip() -> str:
                songs = await client.search_songs("Stephan Bodzin Singularity", limit=1)
                if not songs:
                    raise AppleMusicError("no song to add")
                pl = await client.create_playlist("apple-music-mcp doctor test", "Temporary, safe to delete")
                await client.add_tracks(pl.id, [songs[0].id])
                tracks = await client.playlist_tracks(pl.id)
                try:
                    await client.delete_playlist(pl.id)
                    cleanup = "deleted again"
                except AppleMusicError:
                    cleanup = (
                        "could not delete it via API; delete 'apple-music-mcp doctor test' in the Music app"
                    )
                return f"created {pl.id} with {len(tracks)} track(s), {cleanup}"

            await step("create playlist (write test)", roundtrip)
    finally:
        await client.aclose()
    out("All checks passed." if failures == 0 else f"{failures} check(s) failed.")
    return 0 if failures == 0 else 1


# -------------------------------------------------------------------- install-claude
def claude_desktop_config_path() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if platform.system() == "Windows":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def server_command() -> list[str]:
    """Absolute command for MCP hosts (Claude Desktop does not load your shell PATH)."""
    sibling = Path(sys.executable).parent / "apple-music-mcp"
    exe = str(sibling) if sibling.exists() else shutil.which("apple-music-mcp")
    if exe:
        return [str(Path(exe).absolute()), "serve"]
    return [sys.executable, "-m", "apple_music_mcp"]


def install_claude_desktop(config_path: Path | None = None, command: list[str] | None = None) -> Path:
    config_path = config_path or claude_desktop_config_path()
    command = command or server_command()
    data: dict[str, Any] = {}
    if config_path.exists():
        raw = config_path.read_text() or "{}"
        data = json.loads(raw)
        shutil.copy2(config_path, config_path.with_suffix(".json.bak"))
    data.setdefault("mcpServers", {})["apple-music"] = {"command": command[0], "args": command[1:]}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    return config_path


def install_claude_code_skill(skill_src: Path, dest_root: Path | None = None) -> Path | None:
    if not (skill_src / "SKILL.md").exists():
        return None
    dest = (dest_root or Path.home() / ".claude" / "skills") / "playlist-curator"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(skill_src / "SKILL.md", dest / "SKILL.md")
    return dest


def find_skill_dir() -> Path:
    here = Path(__file__).resolve()
    for candidate in (here.parent / "skill", here.parents[2] / "skill" / "playlist-curator"):
        if (candidate / "SKILL.md").exists():
            return candidate
    return here.parent / "skill"


def do_install_claude(out: Callable[[str], None] = print) -> int:
    cmd = server_command()
    path = install_claude_desktop(command=cmd)
    out(f"Claude Desktop: registered 'apple-music' in {path} (backup: .json.bak). Restart Claude Desktop.")
    skill = install_claude_code_skill(find_skill_dir())
    if skill:
        out(f"Claude Code: skill copied to {skill}")
    out("Claude Code: run this once to add the server:")
    out("  claude mcp add --scope user apple-music -- " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    out("Claude Desktop skill: upload skill/playlist-curator.zip in Settings > Capabilities > Skills.")
    return 0


# ------------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
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
    args = parser.parse_args(argv)

    if args.command in (None, "serve"):
        if args.command is None and sys.stdin.isatty():
            parser.print_help()
            return 0
        from .server import main as serve

        serve()
        return 0
    if args.command == "login":
        return asyncio.run(do_login(args.token, args.storefront, args.browser))
    if args.command == "status":
        print(json.dumps({"file": str(credentials_path()), **load_credentials().redacted()}, indent=2))
        return 0
    if args.command == "doctor":
        return asyncio.run(do_doctor(args.write))
    if args.command == "install-claude":
        return do_install_claude()
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
