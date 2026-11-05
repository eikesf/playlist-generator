"""`apple-music-mcp install-claude`: register the MCP server in Claude Desktop / Claude Code."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from . import Output


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
    for candidate in (here.parents[1] / "skill", here.parents[3] / "skill" / "playlist-curator"):
        if (candidate / "SKILL.md").exists():
            return candidate
    return here.parents[1] / "skill"


def run_install_claude(out: Output = print) -> int:
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
