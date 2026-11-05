from __future__ import annotations

import json
import zipfile
from pathlib import Path

from apple_music_mcp import cli
from apple_music_mcp.commands import install
from apple_music_mcp.commands.doctor import run_doctor
from apple_music_mcp.commands.login import run_login
from apple_music_mcp.config import Credentials, load_credentials, save_credentials

from .conftest import build_app

ROOT = Path(__file__).resolve().parents[1]


async def test_login_with_pasted_token_saves_and_detects_storefront(fake_apple):
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


async def test_login_rejects_invalid_token_and_does_not_save(fake_apple):
    lines: list[str] = []
    code = await run_login("wrong-token-but-long-enough-123", None, False, build=build_app, out=lines.append)
    assert code == 1
    assert "Login check failed" in lines[-1]
    assert load_credentials().media_user_token is None


async def test_login_rejects_garbage(fake_apple):
    lines: list[str] = []
    assert await run_login("abc", None, False, build=build_app, out=lines.append) == 1


async def test_doctor_passes_including_write_roundtrip(fake_apple):
    save_credentials(Credentials(media_user_token=fake_apple.user_token, storefront="br"))
    lines: list[str] = []
    code = await run_doctor(True, build=build_app, out=lines.append)
    text = "\n".join(lines)
    assert code == 0, text
    assert "All checks passed." in text
    assert "create + reorder playlist (write test)" in text
    assert "reorder via 'in_place' verified" in text and "test playlist deleted" in text
    assert fake_apple.playlists == {}


async def test_doctor_fails_without_login(fake_apple):
    lines: list[str] = []
    code = await run_doctor(False, build=build_app, out=lines.append)
    assert code == 1
    assert any("apple-music-mcp login" in line for line in lines)


def test_install_claude_desktop_merges_existing_config(tmp_path):
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


def test_install_claude_desktop_creates_config(tmp_path):
    cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    install.install_claude_desktop(cfg, ["/x/apple-music-mcp", "serve"])
    assert json.loads(cfg.read_text())["mcpServers"]["apple-music"]["args"] == ["serve"]


def test_skill_is_installed_for_claude_code(tmp_path):
    dest = install.install_claude_code_skill(install.find_skill_dir(), tmp_path)
    assert dest and (dest / "SKILL.md").read_text().startswith('---\nname: "playlist-curator"')


def test_skill_zip_is_in_sync_with_skill_md():
    with zipfile.ZipFile(ROOT / "skill" / "playlist-curator.zip") as zf:
        assert zf.namelist() == ["playlist-curator/SKILL.md"]
        assert zf.read("playlist-curator/SKILL.md") == (ROOT / "skill/playlist-curator/SKILL.md").read_bytes()


def test_skill_mentions_every_server_tool():
    from .test_server import EXPECTED_TOOLS

    text = (ROOT / "skill/playlist-curator/SKILL.md").read_text()
    for tool in EXPECTED_TOOLS:
        assert f"`{tool}`" in text, tool


def test_status_and_version(capsys):
    assert cli.main(["status"]) == 0
    assert '"media_user_token": "<missing>"' in capsys.readouterr().out


def test_server_command_is_absolute():
    cmd = install.server_command()
    assert Path(cmd[0]).is_absolute()
    assert cmd[-1] in {"serve", "apple_music_mcp"}


def test_set_bpm_key_saves_and_removes(capsys):
    assert cli.main(["set-bpm-key", "--key", "abc123"]) == 0
    assert load_credentials().getsongbpm_api_key == "abc123"
    assert cli.main(["set-bpm-key", "--key", ""]) == 0
    assert load_credentials().getsongbpm_api_key is None


async def test_doctor_reports_bpm_check(fake_apple):
    fake_apple.deezer_bpm = {"1001": 132.0}
    save_credentials(Credentials(media_user_token=fake_apple.user_token, storefront="br"))
    lines: list[str] = []
    await run_doctor(False, build=build_app, out=lines.append)
    assert any("BPM databases: deezer: 132 BPM" in line for line in lines)


async def test_refresh_command_is_read_only_for_main(fake_apple):
    from apple_music_mcp.commands.refresh import run_refresh
    from apple_music_mcp.local_music import MusicAppReader

    save_credentials(Credentials(media_user_token=fake_apple.user_token, storefront="br"))

    def build(creds):
        a = build_app(creds)
        a.music_app = MusicAppReader(system="Linux")
        return a

    async with build_app(Credentials(media_user_token=fake_apple.user_token)) as a:
        pl = await a.library.create_playlist("Warehouse")
        await a.library.add_tracks(pl.id, ["4001", "1001", "5002"])
    lines: list[str] = []
    assert await run_refresh("Warehouse", build=build, out=lines.append) == 0
    assert "Nada foi removido" in lines[0]
    assert fake_apple.playlists[pl.id]["tracks"] == ["4001", "1001", "5002"]
    assert await run_refresh("Missing", build=build, out=lines.append) == 1


def test_schedule_refresh_writes_launch_agent(tmp_path):
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
