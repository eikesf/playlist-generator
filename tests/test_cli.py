from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from apple_music_mcp import cli
from apple_music_mcp.commands import install
from apple_music_mcp.commands.doctor import run_doctor
from apple_music_mcp.commands.login import run_login
from apple_music_mcp.config import Credentials, load_credentials, save_credentials
from apple_music_mcp.container import AppContainer

from .conftest import build_app
from .fake_apple import FakeAppleMusic

ROOT = Path(__file__).resolve().parents[1]


async def test_login_with_pasted_token_saves_and_detects_storefront(fake_apple: FakeAppleMusic) -> None:
    lines: list[str] = []
    code = await run_login(
        None, None, False, build=build_app, prompt=lambda _: f' "{fake_apple.user_token}" ', out=lines.append
    )
    assert code == 0, lines
    creds = load_credentials()
    assert creds.media_user_token == fake_apple.user_token
    assert creds.developer_token == fake_apple.dev_token
    assert creds.storefront == "br"
    assert fake_apple.user_token not in "\n".join(lines)


async def test_login_rejects_invalid_token_and_does_not_save(fake_apple: FakeAppleMusic) -> None:
    lines: list[str] = []
    code = await run_login("wrong-token-but-long-enough-123", None, False, build=build_app, out=lines.append)
    assert code == 1
    assert "Login check failed" in lines[-1]
    assert load_credentials().media_user_token is None


async def test_login_rejects_garbage(fake_apple: FakeAppleMusic) -> None:
    lines: list[str] = []
    assert await run_login("abc", None, False, build=build_app, out=lines.append) == 1


async def test_doctor_passes_including_write_roundtrip(fake_apple: FakeAppleMusic) -> None:
    save_credentials(Credentials(media_user_token=fake_apple.user_token, storefront="br"))
    lines: list[str] = []
    code = await run_doctor(True, build=build_app, out=lines.append)
    text = "\n".join(lines)
    assert code == 0, text
    assert "All checks passed." in text
    assert "create + reorder playlist (write test)" in text
    assert "reorder via 'in_place' verified" in text and "test playlist deleted" in text
    assert fake_apple.playlists == {}


async def test_doctor_fails_without_login(fake_apple: FakeAppleMusic) -> None:
    lines: list[str] = []
    code = await run_doctor(False, build=build_app, out=lines.append)
    assert code == 1
    assert any("apple-music-mcp login" in line for line in lines)


def test_install_claude_desktop_merges_existing_config(tmp_path: Path) -> None:
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    install.install_claude_desktop(cfg, ["/usr/local/bin/apple-music-mcp", "serve"])
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["apple-music"] == {
        "command": "/usr/local/bin/apple-music-mcp",
        "args": ["serve"],
    }
    assert data["theme"] == "dark"
    assert json.loads(cfg.with_suffix(".json.bak").read_text())["mcpServers"] == {"other": {"command": "x"}}


def test_install_claude_desktop_creates_config(tmp_path: Path) -> None:
    cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    install.install_claude_desktop(cfg, ["/x/apple-music-mcp", "serve"])
    assert json.loads(cfg.read_text())["mcpServers"]["apple-music"]["args"] == ["serve"]


def test_skill_is_installed_for_claude_code(tmp_path: Path) -> None:
    dest = install.install_claude_code_skill(install.find_skill_dir(), tmp_path)
    assert dest and (dest / "SKILL.md").read_text().startswith('---\nname: "playlist-curator"')


def test_skill_zip_is_in_sync_with_skill_md() -> None:
    with zipfile.ZipFile(ROOT / "skill" / "playlist-curator.zip") as zf:
        assert zf.namelist() == ["playlist-curator/SKILL.md"]
        assert zf.read("playlist-curator/SKILL.md") == (ROOT / "skill/playlist-curator/SKILL.md").read_bytes()


def test_skill_mentions_every_server_tool() -> None:
    from .test_server import EXPECTED_TOOLS

    text = (ROOT / "skill/playlist-curator/SKILL.md").read_text()
    for tool in EXPECTED_TOOLS:
        assert f"`{tool}`" in text, tool


def test_status_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["status"]) == 0
    assert '"media_user_token": "<missing>"' in capsys.readouterr().out


def test_server_command_is_absolute() -> None:
    cmd = install.server_command()
    assert Path(cmd[0]).is_absolute()
    assert cmd[-1] in {"serve", "apple_music_mcp"}


def test_set_bpm_key_saves_and_removes(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["set-bpm-key", "--key", "abc123"]) == 0
    assert load_credentials().getsongbpm_api_key == "abc123"
    assert cli.main(["set-bpm-key", "--key", ""]) == 0
    assert load_credentials().getsongbpm_api_key is None


async def test_doctor_reports_bpm_check(fake_apple: FakeAppleMusic) -> None:
    fake_apple.deezer_bpm = {"7001": 126.0}
    save_credentials(Credentials(media_user_token=fake_apple.user_token, storefront="br"))
    lines: list[str] = []
    await run_doctor(False, build=build_app, out=lines.append)
    assert any("BPM databases: deezer: 126 BPM" in line for line in lines)


async def test_refresh_command_is_read_only_for_main(fake_apple: FakeAppleMusic) -> None:
    from apple_music_mcp.commands.refresh import run_refresh
    from apple_music_mcp.local_music import MusicAppReader

    save_credentials(Credentials(media_user_token=fake_apple.user_token, storefront="br"))

    def build(creds: Credentials) -> AppContainer:
        a = build_app(creds)
        a.music_app = MusicAppReader(system="Linux")
        return a

    async with build_app(Credentials(media_user_token=fake_apple.user_token)) as a:
        pl = await a.library.create_playlist("Warehouse")
        await a.library.add_tracks(pl.id, ["4001", "1001", "5002"])
    lines: list[str] = []
    assert await run_refresh("Warehouse", build=build, out=lines.append) == 0
    assert "Nada foi removido" in lines[0] and "1 saem" in lines[0]  # the disliked 5002
    assert fake_apple.playlists[pl.id]["tracks"] == ["4001", "1001", "5002"]
    assert await run_refresh("Missing", build=build, out=lines.append) == 1


def test_schedule_refresh_writes_launch_agent(tmp_path: Path) -> None:
    import plistlib

    from apple_music_mcp.commands.refresh import LABEL, schedule_refresh

    path = schedule_refresh(
        "Warehouse",
        weekday=1,
        hour=10,
        minute=7,
        executable="/x/apple-music-mcp",
        path=tmp_path / "agent.plist",
    )
    data = plistlib.loads(path.read_bytes())
    assert data["Label"] == LABEL
    assert data["ProgramArguments"] == [
        "/x/apple-music-mcp",
        "refresh",
        "--playlist",
        "Warehouse",
        "--notify",
    ]
    assert data["StartCalendarInterval"] == {"Weekday": 1, "Hour": 10, "Minute": 7}


async def test_doctor_covers_unverified_endpoints(fake_apple: FakeAppleMusic) -> None:
    from apple_music_mcp.local_music import MusicAppReader

    save_credentials(Credentials(media_user_token=fake_apple.user_token, storefront="br"))
    async with build_app(Credentials(media_user_token=fake_apple.user_token)) as a:
        await a.library.create_playlist("Warehouse")

    def build(creds: Credentials) -> AppContainer:
        a = build_app(creds)
        a.music_app = MusicAppReader(system="Linux")  # AppleScript unavailable in CI: must be a warning only
        return a

    lines: list[str] = []
    code = await run_doctor(True, build=build, out=lines.append)
    text = "\n".join(lines)
    assert code == 0, text
    for name in ("recently played", "song ratings", "heavy rotation", "similar artists"):
        assert f"[ok]   {name}" in text, name
    assert "[warn] Music app stats via AppleScript" in text
    assert "removal request shape: query" in text


async def test_doctor_reads_stats_from_a_playlist_the_music_app_has(fake_apple: FakeAppleMusic) -> None:
    from apple_music_mcp.local_music import SEP, MusicAppReader

    save_credentials(Credentials(media_user_token=fake_apple.user_token, storefront="br"))
    async with build_app(Credentials(media_user_token=fake_apple.user_token)) as a:
        await a.library.create_playlist("Brown Noise")  # listed by the API, absent from the Music app
        await a.library.create_playlist("Workout")

    line = SEP.join(["Duality", "Slipknot", "12", "0", "true", "false", ""])

    async def runner(args: list[str]) -> tuple[int, str, str]:
        if args[0] == "names":
            return 0, "Music\nWorkout\n", ""
        if args[1] != "Workout":
            return 1, "", "Can’t get user playlist 1. (-1728)"
        return 0, line + "\n", ""

    def build(creds: Credentials) -> AppContainer:
        a = build_app(creds)
        a.music_app = MusicAppReader(runner=runner, system="Darwin")
        return a

    lines: list[str] = []
    assert await run_doctor(False, build=build, out=lines.append) == 0
    text = "\n".join(lines)
    assert (
        "[ok]   Music app stats via AppleScript (macOS): 'Workout': 1 tracks, 1 with plays, 1 favorites"
        in text
    )
    assert "1 playlist(s) not in the Music app skipped" in text
