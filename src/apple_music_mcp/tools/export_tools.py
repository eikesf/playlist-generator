"""Offline fallback: export a track list as text for Tune My Music."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer, export_dir: Path) -> None:
    @mcp.tool()
    async def export_playlist_text(name: str, tracks: list[str]) -> dict[str, Any]:
        """Fallback when the library is unreachable: save tracks as a .txt (one "Artist - Title" per line)
        ready for Tune My Music → Apple Music. Returns the file path and the text."""
        safe = re.sub(r"[^\w\- ]+", "", name).strip() or "playlist"
        export_dir.mkdir(parents=True, exist_ok=True)
        path = export_dir / f"{safe}.txt"
        text = "\n".join(t.strip() for t in tracks if t.strip()) + "\n"
        path.write_text(text, encoding="utf-8")
        return {"path": str(path), "lines": text.count("\n"), "text": text}
