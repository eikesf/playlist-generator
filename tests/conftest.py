from __future__ import annotations

import base64
import json
import shutil
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import respx
from mcp.types import CallToolResult, TextContent

from apple_music_mcp import config
from apple_music_mcp.config import Credentials

if TYPE_CHECKING:
    from apple_music_mcp.container import AppContainer

    from .fake_apple import FakeAppleMusic


def result_text(result: CallToolResult) -> str:
    """Text of a tool result's first content block (errors come back as text)."""
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def make_jwt(exp: float | None = None) -> str:
    def b64(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    payload: dict[str, Any] = {"iss": "TEST", "iat": int(time.time())}
    if exp is not None:
        payload["exp"] = int(exp)
    return f"{b64({'alg': 'ES256', 'kid': 'X'})}.{b64(payload)}.c2lnbmF0dXJl"


@pytest.fixture
def jwt_factory() -> Callable[[float | None], str]:
    return make_jwt


@pytest.fixture(scope="session")
def migrated_db_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One migrated database per test run; tests get a copy instead of re-running migrations."""
    from apple_music_mcp.db import Database

    path = tmp_path_factory.mktemp("db-template") / "template.db"
    Database.open(path).dispose()
    return path


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, migrated_db_template: Path) -> Path:
    """Every test gets its own config dir (with a fresh, migrated database) and no token env vars."""
    from apple_music_mcp.db import DB_FILE

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    shutil.copy2(migrated_db_template, cfg / DB_FILE)
    monkeypatch.setenv(config.ENV_CONFIG_DIR, str(cfg))
    # Never touch the developer's real Keychain from tests; keychain behaviour is tested with
    # an in-memory backend (tests/test_secrets_store.py).
    from apple_music_mcp.secrets import ENV_SECRET_BACKEND

    monkeypatch.setenv(ENV_SECRET_BACKEND, "file")
    for var in (config.ENV_USER_TOKEN, config.ENV_DEVELOPER_TOKEN, config.ENV_STOREFRONT):
        monkeypatch.delenv(var, raising=False)
    return cfg


@pytest.fixture
def fake_apple(respx_mock: respx.MockRouter) -> FakeAppleMusic:
    from .fake_apple import FakeAppleMusic

    return FakeAppleMusic().install(respx_mock)


async def no_sleep(_: float) -> None:
    return None


def build_app(creds: config.Credentials, **kwargs: Any) -> AppContainer:
    from apple_music_mcp.container import AppContainer

    return AppContainer(creds, sleep=no_sleep, **kwargs)


@pytest.fixture
def logged_in_creds(fake_apple: FakeAppleMusic) -> Credentials:
    return config.Credentials(media_user_token=fake_apple.user_token, storefront=fake_apple.storefront)


@pytest.fixture
async def app(logged_in_creds: Credentials) -> AsyncIterator[AppContainer]:
    """A container with a valid user token and no developer token (it must scrape one)."""
    async with build_app(logged_in_creds) as container:
        yield container
