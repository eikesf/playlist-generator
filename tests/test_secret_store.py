"""Secrets in the Keychain (in-memory stand-in here), configuration in .env."""

from __future__ import annotations

import json
import os
import stat

from apple_music_mcp.config import ENV_USER_TOKEN, Credentials, CredentialStore
from apple_music_mcp.secrets import ENV_SECRET_BACKEND, MemoryBackend, default_backend
from apple_music_mcp.settings import AppSettings, load_env_file

TOKEN = "user-token-" + "x" * 30


def test_save_puts_secrets_in_keychain_and_nothing_secret_in_the_file(tmp_path):
    backend = MemoryBackend()
    store = CredentialStore(tmp_path / "credentials.json", secrets=backend)
    store.save(Credentials(media_user_token=TOKEN, storefront="br", getsongbpm_api_key="bpm-key-123456789"))

    on_disk = json.loads((tmp_path / "credentials.json").read_text())
    assert on_disk["media_user_token"] is None and on_disk["getsongbpm_api_key"] is None
    assert on_disk["storefront"] == "br"
    assert TOKEN not in (tmp_path / "credentials.json").read_text()
    assert stat.S_IMODE((tmp_path / "credentials.json").stat().st_mode) == 0o600
    assert backend.values["media_user_token"] == TOKEN

    loaded = store.load()
    assert loaded.media_user_token == TOKEN and loaded.storefront == "br"


def test_load_moves_legacy_file_secrets_into_the_keychain(tmp_path):
    path = tmp_path / "credentials.json"
    CredentialStore(path, secrets=None).save(Credentials(media_user_token=TOKEN, storefront="us"))
    assert TOKEN in path.read_text()

    backend = MemoryBackend()
    creds = CredentialStore(path, secrets=backend).load()
    assert creds.media_user_token == TOKEN
    assert backend.values["media_user_token"] == TOKEN
    assert TOKEN not in path.read_text()  # removed from the file only after the keychain had it
    assert json.loads(path.read_text())["storefront"] == "us"


class _BrokenBackend(MemoryBackend):
    def set(self, key: str, value: str) -> None:  # silently loses writes
        return None


def test_file_secret_is_kept_when_the_keychain_does_not_store_it(tmp_path):
    path = tmp_path / "credentials.json"
    CredentialStore(path, secrets=None).save(Credentials(media_user_token=TOKEN))
    creds = CredentialStore(path, secrets=_BrokenBackend()).load()
    assert creds.media_user_token == TOKEN
    assert TOKEN in path.read_text()  # never lose the only copy


def test_environment_variable_wins_and_is_not_copied(tmp_path, monkeypatch):
    backend = MemoryBackend()
    backend.set("media_user_token", "keychain-token-" + "y" * 30)
    monkeypatch.setenv(ENV_USER_TOKEN, TOKEN)
    creds = CredentialStore(tmp_path / "credentials.json", secrets=backend).load()
    assert creds.media_user_token == TOKEN
    assert backend.values["media_user_token"] != TOKEN


def test_backend_choice(monkeypatch):
    monkeypatch.setenv(ENV_SECRET_BACKEND, "file")
    assert default_backend() is None
    monkeypatch.setenv(ENV_SECRET_BACKEND, "keychain")
    backend = default_backend()
    assert backend is not None and backend.name == "keychain"


def test_env_file_fills_settings_without_overriding_real_variables(isolated_config, monkeypatch):
    for key in ("NTFY_TOPIC", "RECENT_PLAYED_TIMES", "BACKUP_DIR", "APPLE_WEB_API_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APPLE_WEB_API_ENABLED", "true")
    (isolated_config / ".env").write_text(
        "NTFY_TOPIC=random-topic-abc\nRECENT_PLAYED_TIMES=10:30, 12:30,25:00,22:30\n"
        "BACKUP_DIR=~/backups\nAPPLE_WEB_API_ENABLED=false\n"
    )
    loaded = load_env_file()
    try:
        assert "APPLE_WEB_API_ENABLED" not in loaded
        settings = AppSettings.from_env()
        assert settings.ntfy_topic == "random-topic-abc"
        assert settings.recent_played_times == ("10:30", "12:30", "22:30")  # 25:00 ignored
        assert settings.backup_dir == os.path.expanduser("~/backups") or str(settings.backup_dir).endswith(
            "backups"
        )
        assert settings.apple_web_api_enabled is True  # the real variable won
        assert settings.redacted()["ntfy_topic"] == "set"
    finally:
        for key in loaded:
            monkeypatch.delenv(key, raising=False)


def test_defaults_follow_the_guide():
    s = AppSettings()
    assert (s.timezone, s.known_min_plays, s.refresh_auto_apply) == ("America/Sao_Paulo", 10, False)
    assert (s.backup_keep_daily, s.backup_keep_weekly) == (7, 4)
    assert s.recent_played_times == ("10:30", "22:30")


async def test_kill_switch_blocks_the_web_api_and_search_falls_back(fake_apple, logged_in_creds):
    from dataclasses import replace

    from apple_music_mcp.apple.errors import WebApiDisabled

    from .conftest import build_app

    async with build_app(
        logged_in_creds, settings=replace(AppSettings(), apple_web_api_enabled=False)
    ) as app:
        try:
            await app.library.storefront()
        except WebApiDisabled as exc:
            assert "APPLE_WEB_API_ENABLED" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("the web API must be blocked")
        songs = await app.catalog.search_songs("Amelie Lens", limit=3)
        assert songs  # served by the iTunes Search fallback


def test_token_expiry_is_recorded_or_estimated(isolated_config):
    from datetime import UTC, datetime

    from apple_music_mcp.db import Database
    from apple_music_mcp.db.settings import SettingsStore
    from apple_music_mcp.token_expiry import parse_expiry, record_user_token_expiry, user_token_expiry

    db = Database.open()
    state = SettingsStore(db)
    now = datetime(2026, 11, 6, tzinfo=UTC)
    estimated = record_user_token_expiry(state, None, now)
    assert estimated["estimated"] is True and estimated["expires_at"].startswith("2027-05-05")
    record_user_token_expiry(state, parse_expiry("2027-05-04"), now)
    info = user_token_expiry(state, now)
    assert info is not None and info["estimated"] is False and info["days_left"] == 179
    assert parse_expiry("not a date") is None and parse_expiry("") is None
    db.dispose()
