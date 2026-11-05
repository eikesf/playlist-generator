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


@pytest.fixture
def fake_apple(respx_mock):
    from .fake_apple import FakeAppleMusic

    return FakeAppleMusic().install(respx_mock)


@pytest.fixture
async def client(fake_apple):
    """A client with a valid user token and no developer token (it must scrape one)."""
    from apple_music_mcp.client import AppleMusicClient

    async def no_sleep(_: float) -> None:
        return None

    creds = config.Credentials(media_user_token=fake_apple.user_token, storefront=fake_apple.storefront)
    async with AppleMusicClient(creds, sleep=no_sleep) as c:
        yield c
