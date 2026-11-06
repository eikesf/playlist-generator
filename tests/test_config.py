from __future__ import annotations

import json
import stat
import time

from apple_music_mcp import config
from apple_music_mcp.config import Credentials, jwt_expiry, load_credentials, mask, save_credentials


def test_defaults_when_nothing_stored():
    creds = load_credentials()
    assert creds.media_user_token is None
    assert creds.developer_token is None
    assert creds.storefront == "br"


def test_save_and_load_roundtrip_with_owner_only_permissions(isolated_config, jwt_factory):
    token = jwt_factory(time.time() + 86400)
    creds = Credentials(media_user_token="mut-123456789abc", developer_token=token, storefront="US")
    path = save_credentials(creds)
    assert path == isolated_config / "credentials.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = load_credentials()
    assert loaded.media_user_token == "mut-123456789abc"
    assert loaded.developer_token == token
    assert loaded.storefront == "us"


def test_env_overrides_file(monkeypatch):
    save_credentials(Credentials(media_user_token="from-file", storefront="br"))
    monkeypatch.setenv(config.ENV_USER_TOKEN, "  from-env  ")
    monkeypatch.setenv(config.ENV_STOREFRONT, "GB")
    creds = load_credentials()
    assert creds.media_user_token == "from-env"
    assert creds.storefront == "gb"


def test_corrupted_file_falls_back_to_defaults(isolated_config):
    isolated_config.mkdir(parents=True, exist_ok=True)
    (isolated_config / "credentials.json").write_text("{not json")
    assert load_credentials().media_user_token is None


def test_jwt_expiry_and_validity(jwt_factory):
    exp = time.time() + 7200
    token = jwt_factory(exp)
    assert jwt_expiry(token) == int(exp)
    assert config.is_jwt(token)
    assert Credentials(developer_token=token).developer_token_valid()
    expired = jwt_factory(time.time() - 10)
    assert not Credentials(developer_token=expired).developer_token_valid()
    assert jwt_expiry("not.a.jwt") is None
    assert not config.is_jwt("abc")
    assert not Credentials().developer_token_valid()


def test_mask_never_reveals_full_secret():
    secret = "A" * 40
    masked = mask(secret)
    assert secret not in masked
    assert masked.startswith("AAAAAA...")
    assert mask(None) == "<missing>"
    assert mask("short") == "***"


def test_redacted_contains_no_secrets(jwt_factory):
    token = jwt_factory(time.time() + 100)
    creds = Credentials(media_user_token="X" * 50, developer_token=token)
    dumped = json.dumps(creds.redacted())
    assert "X" * 50 not in dumped
    assert token not in dumped
