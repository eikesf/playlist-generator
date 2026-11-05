from __future__ import annotations

import base64
import json
import time

import pytest

from apple_music_mcp import config


def make_jwt(exp: float | None = None) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    payload = {"iss": "TEST", "iat": int(time.time())}
    if exp is not None:
        payload["exp"] = int(exp)
    return f"{b64({'alg': 'ES256', 'kid': 'X'})}.{b64(payload)}.c2lnbmF0dXJl"


@pytest.fixture
def jwt_factory():
    return make_jwt


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Every test gets its own empty config dir and no token env vars."""
    monkeypatch.setenv(config.ENV_CONFIG_DIR, str(tmp_path / "cfg"))
    for var in (config.ENV_USER_TOKEN, config.ENV_DEVELOPER_TOKEN, config.ENV_STOREFRONT):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "cfg"
