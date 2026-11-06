"""Non-secret configuration from ``.env`` (guide §5).

The file lives at ``~/.config/apple-music-mcp/.env`` (``.env.example`` in the repository lists every
key). Real environment variables win over the file. Values here are machine configuration; preferences
the owner changes in chat live in the database (``setting`` table) and override these defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from .config import config_dir

ENV_FILE = ".env"
ICLOUD_DRIVE = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"


def env_file_path() -> Path:
    return config_dir() / ENV_FILE


def load_env_file(path: Path | None = None) -> list[str]:
    """Copy ``.env`` values into ``os.environ`` without overriding real variables. Returns the keys set."""
    path = path or env_file_path()
    if not path.exists():
        return []
    loaded = []
    for key, value in dotenv_values(path).items():
        if value is not None and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _weekday(name: str, default: str) -> str:
    raw = os.environ.get(name, "").strip().lower()
    return raw if raw in WEEKDAYS else default


def _ratio(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if 0 <= value <= 1 else default


def _str(name: str, default: str | None = None) -> str | None:
    raw = os.environ.get(name, "").strip()
    return raw or default


def _times(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, "").strip() or default
    out = []
    for part in raw.split(","):
        hh, _, mm = part.strip().partition(":")
        if hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60:
            out.append(f"{int(hh):02d}:{int(mm):02d}")
    return out


@dataclass(frozen=True)
class AppSettings:
    timezone: str = "America/Sao_Paulo"
    # Kill switch (risk assessment §5.8): false = never call the Apple web-player API.
    apple_web_api_enabled: bool = True
    # Notifications and dead man's switch (guide §6). The ntfy topic is treated as a secret.
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str | None = None
    healthcheck_url: str | None = None
    healthchecks_api_key: str | None = None
    # Backups (guide §6.5).
    backup_dir: Path = field(default_factory=lambda: ICLOUD_DRIVE / "apple-music-mcp-backups")
    backup_keep_daily: int = 7
    backup_keep_weekly: int = 4
    # Schedules (local time, HH:MM).
    recent_played_times: tuple[str, ...] = ("10:30", "22:30")
    snapshot_time: str = "23:40"
    doctor_time: str = "08:10"
    backup_time: str = "23:50"
    heartbeat_minutes: int = 10
    # Behaviour defaults (guide §7); chat preferences in the database override them.
    known_min_plays: int = 10
    refresh_auto_apply: bool = False
    token_reminder_days: int = 7
    # Weekly refresh (guide §7.10): default slot (local time), evidence window and thresholds.
    refresh_weekday: str = "monday"
    refresh_time: str = "09:00"
    refresh_window_days: int = 42
    refresh_min_history_days: int = 14
    refresh_min_days_in_playlist: int = 14
    refresh_min_skips: int = 3
    refresh_skip_ratio: float = 0.5
    proposal_ttl_days: int = 7
    # Weekly Discoveries (guide §7.11): target batch size (accepted between 2/3 and 4/3 of it).
    discoveries_size: int = 30
    # Promotion of liked discoveries (guide §7.11): "liked" criterion and weekly cap.
    promote_min_plays: int = 3
    promote_max_skip_ratio: float = 0.25
    promote_weekly_fraction: float = 0.2
    promote_weekly_max: int = 30

    @classmethod
    def from_env(cls) -> AppSettings:
        d = cls()
        backup = _str("BACKUP_DIR")
        return cls(
            timezone=_str("TIMEZONE", d.timezone) or d.timezone,
            apple_web_api_enabled=_bool("APPLE_WEB_API_ENABLED", d.apple_web_api_enabled),
            ntfy_server=(_str("NTFY_SERVER", d.ntfy_server) or d.ntfy_server).rstrip("/"),
            ntfy_topic=_str("NTFY_TOPIC"),
            healthcheck_url=_str("HEALTHCHECK_URL"),
            healthchecks_api_key=_str("HEALTHCHECKS_API_KEY"),
            backup_dir=Path(backup).expanduser() if backup else d.backup_dir,
            backup_keep_daily=_int("BACKUP_KEEP_DAILY", d.backup_keep_daily),
            backup_keep_weekly=_int("BACKUP_KEEP_WEEKLY", d.backup_keep_weekly),
            recent_played_times=tuple(_times("RECENT_PLAYED_TIMES", ",".join(d.recent_played_times))),
            snapshot_time=(_times("SNAPSHOT_TIME", d.snapshot_time) or [d.snapshot_time])[0],
            doctor_time=(_times("DOCTOR_TIME", d.doctor_time) or [d.doctor_time])[0],
            backup_time=(_times("BACKUP_TIME", d.backup_time) or [d.backup_time])[0],
            heartbeat_minutes=max(5, _int("HEARTBEAT_MINUTES", d.heartbeat_minutes)),
            known_min_plays=_int("KNOWN_MIN_PLAYS", d.known_min_plays),
            refresh_auto_apply=_bool("REFRESH_AUTO_APPLY", d.refresh_auto_apply),
            token_reminder_days=_int("TOKEN_REMINDER_DAYS", d.token_reminder_days),
            refresh_weekday=_weekday("REFRESH_WEEKDAY", d.refresh_weekday),
            refresh_time=(_times("REFRESH_TIME", d.refresh_time) or [d.refresh_time])[0],
            refresh_window_days=max(7, _int("REFRESH_WINDOW_DAYS", d.refresh_window_days)),
            refresh_min_history_days=max(1, _int("REFRESH_MIN_HISTORY_DAYS", d.refresh_min_history_days)),
            refresh_min_days_in_playlist=max(
                0, _int("REFRESH_MIN_DAYS_IN_PLAYLIST", d.refresh_min_days_in_playlist)
            ),
            refresh_min_skips=max(1, _int("REFRESH_MIN_SKIPS", d.refresh_min_skips)),
            refresh_skip_ratio=_ratio("REFRESH_SKIP_RATIO", d.refresh_skip_ratio),
            proposal_ttl_days=max(1, _int("PROPOSAL_TTL_DAYS", d.proposal_ttl_days)),
            discoveries_size=max(3, _int("DISCOVERIES_SIZE", d.discoveries_size)),
            promote_min_plays=max(1, _int("PROMOTE_MIN_PLAYS", d.promote_min_plays)),
            promote_max_skip_ratio=_ratio("PROMOTE_MAX_SKIP_RATIO", d.promote_max_skip_ratio),
            promote_weekly_fraction=_ratio("PROMOTE_WEEKLY_FRACTION", d.promote_weekly_fraction),
            promote_weekly_max=max(0, min(30, _int("PROMOTE_WEEKLY_MAX", d.promote_weekly_max))),
        )

    def redacted(self) -> dict[str, object]:
        """For `status`: secrets shown only as set / not set."""
        return {
            "timezone": self.timezone,
            "apple_web_api_enabled": self.apple_web_api_enabled,
            "ntfy_server": self.ntfy_server,
            "ntfy_topic": "set" if self.ntfy_topic else None,
            "healthcheck_url": "set" if self.healthcheck_url else None,
            "healthchecks_api_key": "set" if self.healthchecks_api_key else None,
            "backup_dir": str(self.backup_dir),
            "recent_played_times": list(self.recent_played_times),
            "snapshot_time": self.snapshot_time,
            "doctor_time": self.doctor_time,
            "backup_time": self.backup_time,
            "heartbeat_minutes": self.heartbeat_minutes,
            "known_min_plays": self.known_min_plays,
            "refresh_auto_apply": self.refresh_auto_apply,
            "refresh_weekday": self.refresh_weekday,
            "refresh_time": self.refresh_time,
            "refresh_window_days": self.refresh_window_days,
            "refresh_min_history_days": self.refresh_min_history_days,
            "proposal_ttl_days": self.proposal_ttl_days,
        }
