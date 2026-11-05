"""Build skill/playlist-curator.zip (the format Claude's Skills upload expects)."""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skill" / "playlist-curator"
ZIP_PATH = ROOT / "skill" / "playlist-curator.zip"


def build() -> Path:
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SKILL_DIR.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(f"playlist-curator/{path.relative_to(SKILL_DIR).as_posix()}")
                info.date_time = (2026, 1, 1, 0, 0, 0)  # reproducible
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())
    return ZIP_PATH


if __name__ == "__main__":
    print(build())
