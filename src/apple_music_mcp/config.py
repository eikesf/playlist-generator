"""Local configuration and credential storage.

Secrets (the Apple Music token, the cached developer token, the GetSongBPM key) live in the macOS
Keychain when it is available (``secrets.py``); the rest of the credentials, and the secrets when no
keychain exists, live in ``~/.config/apple-music-mcp/credentials.json`` with 0600 permissions (override
the directory with ``APPLE_MUSIC_MCP_CONFIG_DIR``). Environment variables take precedence so the MCP
host config can inject tokens if preferred:

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

from .secrets import SECRET_FIELDS, SERVICE, SecretBackend, default_backend

ENV_CONFIG_DIR = "APPLE_MUSIC_MCP_CONFIG_DIR"
ENV_USER_TOKEN = "APPLE_MUSIC_USER_TOKEN"
ENV_DEVELOPER_TOKEN = "APPLE_MUSIC_DEVELOPER_TOKEN"
ENV_STOREFRONT = "APPLE_MUSIC_STOREFRONT"
ENV_GETSONGBPM_KEY = "GETSONGBPM_API_KEY"

DEFAULT_STOREFRONT = "br"
CREDENTIALS_FILE = "credentials.json"
STATE_FILE = "state.json"
_DEFAULT = object()  # sentinel: "pick the default secret backend"


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
    """Load/save credentials: secrets in the Keychain when available, the rest in the 0600 file.

    With a secret backend, ``load`` also moves secrets found in an older credentials file into the
    Keychain (written, read back to verify, and only then removed from the file).
    """

    def __init__(self, path: Path | None = None, secrets: SecretBackend | None | object = _DEFAULT):
        self.path = path or credentials_path()
        self.secrets: SecretBackend | None = default_backend() if secrets is _DEFAULT else secrets  # type: ignore[assignment]

    @property
    def location(self) -> str:
        return f"macOS Keychain ({SERVICE}) + {self.path}" if self.secrets else str(self.path)

    def load(self) -> Credentials:
        creds = load_credentials(self.path)
        if self.secrets is None:
            return creds
        file_creds = _file_only(self.path)
        moved = False
        for name in SECRET_FIELDS:
            if _env_override(name):
                continue
            stored = self.secrets.get(name)
            legacy = getattr(file_creds, name)
            if stored:
                setattr(creds, name, stored)
            elif legacy:
                self.secrets.set(name, legacy)
                if self.secrets.get(name) == legacy:
                    moved = True
        if moved:
            write_private_json(self.path, _without_secrets(file_creds))
        return creds

    def save(self, creds: Credentials) -> Path:
        if self.secrets is None:
            return save_credentials(creds, self.path)
        for name in SECRET_FIELDS:
            value = getattr(creds, name)
            if value:
                self.secrets.set(name, value)
            else:
                self.secrets.delete(name)
        creds.saved_at = time.time()
        return write_private_json(self.path, _without_secrets(creds))


_ENV_FOR_FIELD = {
    "media_user_token": ENV_USER_TOKEN,
    "developer_token": ENV_DEVELOPER_TOKEN,
    "getsongbpm_api_key": ENV_GETSONGBPM_KEY,
}


def _env_override(name: str) -> bool:
    return bool(os.environ.get(_ENV_FOR_FIELD[name]))


def _file_only(path: Path) -> Credentials:
    """The credentials file alone, without environment overrides."""
    saved = {k: os.environ.pop(v) for k, v in _ENV_FOR_FIELD.items() if v in os.environ}
    try:
        return load_credentials(path)
    finally:
        for k, v in saved.items():
            os.environ[_ENV_FOR_FIELD[k]] = v


def _without_secrets(creds: Credentials) -> dict:
    data = asdict(creds)
    for name in SECRET_FIELDS:
        data[name] = None
    return data
