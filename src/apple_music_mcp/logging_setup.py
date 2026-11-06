"""Structured logs in a rotating local file (guide §6.6). Never to stdout: stdout is the MCP channel."""

from __future__ import annotations

import json
import logging
import logging.handlers
import platform
from datetime import UTC, datetime
from pathlib import Path

from .config import config_dir
from .redact import redact

LOG_FILE = "app.log"


def log_dir() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Logs" / "apple-music-mcp"
    return config_dir() / "logs"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))[-2000:]
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(directory: Path | None = None, level: int = logging.INFO) -> Path:
    """Attach one rotating JSON file handler (1 MB x 5) to the package logger. Idempotent."""
    directory = directory or log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOG_FILE
    logger = logging.getLogger("apple_music_mcp")
    for handler in logger.handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler) and Path(handler.baseFilename) == path:
            return path
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return path
