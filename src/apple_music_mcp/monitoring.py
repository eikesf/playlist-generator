"""Alerts and the external heartbeat. The monitoring PR fills this in (ntfy, healthchecks.io)."""

from __future__ import annotations

import httpx

from .jobs.core import Notifier, NullNotifier
from .settings import AppSettings


def default_notifier(settings: AppSettings, http: httpx.AsyncClient) -> Notifier:
    return NullNotifier()
