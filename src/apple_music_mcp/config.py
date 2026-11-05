"""Local configuration and credential storage.

Credentials live in ``~/.config/apple-music-mcp/credentials.json`` (override the directory with
``APPLE_MUSIC_MCP_CONFIG_DIR``). The file is written with 0600 permissions. Environment variables
take precedence over the file so the MCP host config can inject tokens if preferred:

- ``APPLE_MUSIC_USER_TOKEN``: the ``media-user-token`` cookie from music.apple.com
- ``APPLE_MUSIC_DEVELOPER_TOKEN``: the web-player developer JWT (normally scraped automatically)
- ``APPLE_MUSIC_STOREFRONT``: two-letter storefront, e.g. ``br`` or ``us``
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ENV_CONFIG_DIR = "APPLE_MUSIC_MCP_CONFIG_DIR"
ENV_USER_TOKEN = "APPLE_MUSIC_USER_TOKEN"
ENV_DEVELOPER_TOKEN = "APPLE_MUSIC_DEVELOPER_TOKEN"
ENV_STOREFRONT = "APPLE_MUSIC_STOREFRONT"
ENV_GETSONGBPM_KEY = "GETSONGBPM_API_KEY"

DEFAULT_STOREFRONT = "br"
CREDENTIALS_FILE = "credentials.json"
STATE_FILE = "state.json"


def config_dir() -> Path:
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "apple-music-mcp"


def credentials_path() -> Path:
    return config_dir() / CREDENTIALS_FILE


def jwt_expiry(token: str) -> int | None:
    """Return the ``exp`` claim (unix seconds) of a JWT without verifying it, or None."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = data.get("exp") if isinstance(data, dict) else None
    return int(exp) if isinstance(exp, (int, float)) else None


def is_jwt(token: str) -> bool:
    return token.startswith("eyJ") and token.count(".") == 2 and jwt_expiry(token) is not None


def mask(secret: str | None) -> str:
    """Render a secret safely for logs and tool output."""
    if not secret:
        return "<missing>"
    if len(secret) <= 12:
        return "***"
    return f"{secret[:6]}...{secret[-4:]} ({len(secret)} chars)"


@dataclass
class Credentials:
    media_user_token: str | None = None
    developer_token: str | None = None
    storefront: str = DEFAULT_STOREFRONT
    saved_at: float = field(default_factory=time.time)
    getsongbpm_api_key: str | None = None

    @property
    def developer_token_expires_at(self) -> int | None:
        return jwt_expiry(self.developer_token) if self.developer_token else None

    def developer_token_valid(self, now: float | None = None, margin: int = 3600) -> bool:
        exp = self.developer_token_expires_at
        if exp is None:
            return False
        return exp - margin > (now if now is not None else time.time())

    def redacted(self) -> dict:
        exp = self.developer_token_expires_at
        return {
            "media_user_token": mask(self.media_user_token),
            "developer_token": mask(self.developer_token),
            "developer_token_expires_at": exp,
            "storefront": self.storefront,
            "getsongbpm_api_key": mask(self.getsongbpm_api_key) if self.getsongbpm_api_key else None,
        }


def load_credentials(path: Path | None = None) -> Credentials:
    """Load credentials from disk, then apply environment overrides."""
    path = path or credentials_path()
    creds = Credentials()
    if path.exists():
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError:
            raw = {}
        if isinstance(raw, dict):
            creds = Credentials(
                media_user_token=raw.get("media_user_token") or None,
                developer_token=raw.get("developer_token") or None,
                storefront=(raw.get("storefront") or DEFAULT_STOREFRONT).lower(),
                saved_at=float(raw.get("saved_at") or time.time()),
                getsongbpm_api_key=raw.get("getsongbpm_api_key") or None,
            )
    if os.environ.get(ENV_USER_TOKEN):
        creds.media_user_token = os.environ[ENV_USER_TOKEN].strip()
    if os.environ.get(ENV_DEVELOPER_TOKEN):
        creds.developer_token = os.environ[ENV_DEVELOPER_TOKEN].strip()
    if os.environ.get(ENV_STOREFRONT):
        creds.storefront = os.environ[ENV_STOREFRONT].strip().lower()
    if os.environ.get(ENV_GETSONGBPM_KEY):
        creds.getsongbpm_api_key = os.environ[ENV_GETSONGBPM_KEY].strip()
    return creds


def write_private_json(path: Path, data: dict) -> Path:
    """Write JSON atomically with owner-only permissions (dir 0700, file 0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def save_credentials(creds: Credentials, path: Path | None = None) -> Path:
    """Persist credentials atomically with owner-only permissions."""
    creds.saved_at = time.time()
    return write_private_json(path or credentials_path(), asdict(creds))


class CredentialStore:
    """Load/save credentials at a fixed path (injectable for tests and the CLI)."""

    def __init__(self, path: Path | None = None):
        self.path = path or credentials_path()

    def load(self) -> Credentials:
        return load_credentials(self.path)

    def save(self, creds: Credentials) -> Path:
        return save_credentials(creds, self.path)


class StateStore:
    """Small non-secret runtime state (e.g. which playlist-edit strategy works on this account)."""

    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / STATE_FILE

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str, default=None):
        return self.load().get(key, default)

    def set(self, key: str, value) -> None:
        data = self.load()
        data[key] = value
        write_private_json(self.path, data)
