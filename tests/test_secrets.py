"""Guardrail: no credential may ever be committed. Runs in CI on every push/PR."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Patterns are assembled at runtime so this file does not match itself.
PATTERNS = {
    "JWT (developer token)": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "private key": re.compile("-----BEGIN " + r"(?:EC |RSA |OPENSSH )?PRIVATE KEY-----"),
    "media-user-token value": re.compile(
        r"media[-_]user[-_]token" + r"""["']?\s*[:=]\s*["'][A-Za-z0-9+/=_-]{40,}""", re.I
    ),
    "filled APPLE_MUSIC_* variable": re.compile(r"^APPLE_MUSIC_(?:USER|DEVELOPER)_TOKEN=\S{20,}", re.M),
}
FORBIDDEN_FILES = re.compile(r"(^|/)(\.env(\..+)?|credentials\.json|.+\.(p8|pem|key|p12))$")
ALLOWED_FILES = {".env.example"}


def tracked_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [f for f in out.splitlines() if f]


def test_no_secret_files_are_tracked() -> None:
    bad = [f for f in tracked_files() if FORBIDDEN_FILES.search(f) and f not in ALLOWED_FILES]
    assert not bad, f"secret-like files are tracked: {bad}"


def test_no_secret_values_in_tracked_files() -> None:
    hits = []
    for name in tracked_files():
        path = ROOT / name
        if not path.is_file() or path.suffix in {".zip", ".png", ".jpg"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{name}: {label}" for label, rx in PATTERNS.items() if rx.search(text)]
    assert not hits, "possible credentials committed:\n" + "\n".join(hits)


def test_gitignore_blocks_credentials() -> None:
    probes = [
        ".env",
        ".env.local",
        "credentials.json",
        "browser-profile/Cookies",
        "AuthKey_X.p8",
        "x/apple-music-mcp/a",
    ]
    out = subprocess.run(["git", "check-ignore", *probes], cwd=ROOT, capture_output=True, text=True).stdout
    assert sorted(out.split()) == sorted(probes)
    assert subprocess.run(["git", "check-ignore", ".env.example"], cwd=ROOT).returncode == 1


def test_patterns_detect_real_shapes() -> None:
    fake_jwt = "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + "." + "c" * 20
    assert PATTERNS["JWT (developer token)"].search(fake_jwt)
    assert PATTERNS["media-user-token value"].search('"media-user-token": "' + "A" * 60 + '"')
    assert PATTERNS["filled APPLE_MUSIC_* variable"].search("APPLE_MUSIC_USER_TOKEN=" + "x" * 30)
