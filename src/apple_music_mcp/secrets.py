"""Where secrets live: the macOS Keychain (via ``keyring``), or the 0600 credentials file as a fallback.

Guide §5: the Apple Music token is stored in the Keychain. Non-secret configuration goes to ``.env``.
``APPLE_MUSIC_MCP_SECRET_BACKEND`` forces a backend: ``keychain`` or ``file`` (tests and CI use ``file``).
"""

from __future__ import annotations

import contextlib
import os
from typing import Protocol

SERVICE = "apple-music-mcp"
ENV_SECRET_BACKEND = "APPLE_MUSIC_MCP_SECRET_BACKEND"
# Credential fields that are secrets. Everything else in Credentials is plain configuration.
SECRET_FIELDS = ("media_user_token", "developer_token", "getsongbpm_api_key")


class SecretBackend(Protocol):
    name: str

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class KeyringBackend:
    """macOS Keychain (or whatever OS keyring ``keyring`` picks)."""

    name = "keychain"

    def __init__(self, service: str = SERVICE):
        self.service = service

    def get(self, key: str) -> str | None:
        import keyring

        return keyring.get_password(self.service, key)

    def set(self, key: str, value: str) -> None:
        import keyring

        keyring.set_password(self.service, key, value)

    def delete(self, key: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        with contextlib.suppress(PasswordDeleteError):
            keyring.delete_password(self.service, key)


class MemoryBackend:
    """In-memory backend for tests."""

    name = "memory"

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


def keyring_usable() -> bool:
    """True when ``keyring`` has a real backend (the Keychain on macOS), not the 'fail' placeholder."""
    try:
        import keyring
    except ImportError:  # pragma: no cover - keyring is a dependency
        return False
    backend = keyring.get_keyring()
    module = type(backend).__module__
    return not (module.endswith(".fail") or module.endswith(".null"))


def default_backend() -> SecretBackend | None:
    """The Keychain when available; None means "keep secrets in the 0600 credentials file"."""
    choice = os.environ.get(ENV_SECRET_BACKEND, "").strip().lower()
    if choice == "file":
        return None
    if choice == "keychain" or keyring_usable():
        return KeyringBackend()
    return None
