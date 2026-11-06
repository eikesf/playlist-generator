"""`apple-music-mcp status`: where things are stored and what is configured (secrets masked)."""

from __future__ import annotations

import json

from ..config import CredentialStore
from ..db import Database
from ..db.settings import SettingsStore
from ..settings import AppSettings, env_file_path
from ..token_expiry import user_token_expiry
from . import Output


def run_status(*, store: CredentialStore | None = None, out: Output = print) -> int:
    store = store or CredentialStore()
    db = Database.open()
    try:
        expiry = user_token_expiry(SettingsStore(db))
    finally:
        db.dispose()
    report = {
        "credentials": {"stored_in": store.location, **store.load().redacted()},
        "user_token_expiry": expiry,
        "database": str(db.path),
        "env_file": str(env_file_path()) + ("" if env_file_path().exists() else " (not created yet)"),
        "settings": AppSettings.from_env().redacted(),
    }
    out(json.dumps(report, indent=2, default=str))
    return 0
