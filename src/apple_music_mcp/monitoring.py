"""Alerts (ntfy) and the dead man's switch (healthchecks.io), guide §6. Both free; both optional.

Messages are built by this code and pass through ``redact`` before leaving the Mac: no tokens,
ever. A failed delivery never breaks a job; it is logged and the job carries on.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from urllib.parse import urlparse

import httpx

from .jobs.core import Notifier, NullNotifier
from .redact import redact
from .settings import AppSettings

log = logging.getLogger(__name__)

HEALTHCHECKS_API = "https://healthchecks.io/api/v3"
_PRIORITY = {"low": "2", "default": "3", "high": "4", "urgent": "5"}


class NtfyNotifier:
    """Push to the owner's phone through ntfy.sh (or a self-hosted server)."""

    def __init__(self, http: httpx.AsyncClient, server: str, topic: str):
        self._http = http
        self._url = f"{server.rstrip('/')}/{topic}"

    async def send(
        self, title: str, message: str, *, priority: str = "default", tags: tuple[str, ...] = ()
    ) -> bool:
        headers = {"Title": redact(title)[:200], "Priority": _PRIORITY.get(priority, "3")}
        if tags:
            headers["Tags"] = ",".join(tags)
        try:
            resp = await self._http.post(
                self._url, content=redact(message)[:3000].encode(), headers=headers, timeout=10
            )
        except httpx.HTTPError as exc:
            log.warning("ntfy delivery failed: %s", type(exc).__name__)
            return False
        if resp.status_code >= 400:
            log.warning("ntfy delivery failed: HTTP %s", resp.status_code)
            return False
        return True


def default_notifier(settings: AppSettings, http: httpx.AsyncClient) -> Notifier:
    if settings.ntfy_topic:
        return NtfyNotifier(http, settings.ntfy_server, settings.ntfy_topic)
    return NullNotifier()


def check_uuid(ping_url: str) -> str | None:
    """The check's UUID is the last path segment of its ping URL (https://hc-ping.com/<uuid>)."""
    part = urlparse(ping_url).path.rstrip("/").rsplit("/", 1)[-1]
    return part if len(part) == 36 and part.count("-") == 4 else None


class Healthchecks:
    def __init__(self, http: httpx.AsyncClient, ping_url: str, api_key: str | None = None):
        self._http = http
        self.ping_url = ping_url.rstrip("/")
        self._api_key = api_key

    async def ping(self, ok: bool = True, message: str = "") -> bool:
        url = self.ping_url if ok else f"{self.ping_url}/fail"
        try:
            resp = await self._http.post(url, content=redact(message)[:1000].encode(), timeout=10)
        except httpx.HTTPError as exc:
            log.warning("healthchecks ping failed: %s", type(exc).__name__)
            return False
        return resp.status_code < 400

    async def pause(self) -> bool:
        """Pause the check through the API (needs a read-write API key). [VERIFY live]"""
        uuid = check_uuid(self.ping_url)
        if not (uuid and self._api_key):
            return False
        try:
            resp = await self._http.post(
                f"{HEALTHCHECKS_API}/checks/{uuid}/pause", headers={"X-Api-Key": self._api_key}, timeout=10
            )
        except httpx.HTTPError:
            return False
        return resp.status_code < 400


def claude_desktop_running() -> bool | None:
    """True/False on macOS (Dispatch needs the app open, guide §6.1); None elsewhere."""
    if platform.system() != "Darwin":
        return None
    result = subprocess.run(["pgrep", "-x", "Claude"], capture_output=True, check=False)
    return result.returncode == 0
