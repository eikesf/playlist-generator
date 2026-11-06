"""Guide §6: ntfy alerts, healthchecks heartbeat, pause mode, token reminders, redacted logs."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import respx

from apple_music_mcp.config import Credentials
from apple_music_mcp.db.repositories import JobOutcome
from apple_music_mcp.jobs import NullNotifier, run_job
from apple_music_mcp.jobs.doctor import remind_token_expiry
from apple_music_mcp.jobs.heartbeat import heartbeat_job
from apple_music_mcp.logging_setup import setup_logging
from apple_music_mcp.monitoring import Healthchecks, NtfyNotifier, check_uuid, default_notifier
from apple_music_mcp.pause import pause
from apple_music_mcp.redact import redact
from apple_music_mcp.settings import AppSettings
from apple_music_mcp.token_expiry import record_user_token_expiry

from .conftest import build_app, make_jwt

UUID = "0f6a1c2e-1234-4abc-9def-0123456789ab"
PING = f"https://hc-ping.com/{UUID}"


def test_redact_removes_tokens_but_keeps_the_message() -> None:
    jwt = make_jwt(9_999_999_999)
    blob = "Ab3" * 70  # token-like: long, mixed case and digits
    text = f"failed with Bearer {jwt} and media-user-token={blob} for Amelie Lens; raw {blob}"
    clean = redact(text)
    assert jwt not in clean and blob not in clean
    assert "Amelie Lens" in clean and "failed with" in clean
    path = "/home/user/playlist-generator/src/apple_music_mcp/jobs/snapshot_and_more_words.py"
    assert redact(f"File {path}") == f"File {path}"  # paths stay readable


async def test_ntfy_sends_redacted_alerts(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post("https://ntfy.sh/topic-abc").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as http:
        notifier = NtfyNotifier(http, "https://ntfy.sh", "topic-abc")
        assert await notifier.send(
            "doctor failed", "token eyJabcdefghijk.eyJabcdefghijk.sig leaked?", priority="high"
        )
    request = route.calls.last.request
    assert request.headers["Title"] == "doctor failed" and request.headers["Priority"] == "4"
    assert b"eyJabcdefghijk" not in request.content


async def test_ntfy_failure_never_raises(respx_mock: respx.MockRouter) -> None:
    respx_mock.post("https://ntfy.sh/t").mock(side_effect=httpx.ConnectError("offline"))
    async with httpx.AsyncClient() as http:
        assert await NtfyNotifier(http, "https://ntfy.sh", "t").send("x", "y") is False


def test_default_notifier_needs_a_topic() -> None:
    http = httpx.AsyncClient()
    assert isinstance(default_notifier(AppSettings(), http), NullNotifier)
    assert isinstance(default_notifier(replace(AppSettings(), ntfy_topic="t"), http), NtfyNotifier)


def test_check_uuid_from_ping_url() -> None:
    assert check_uuid(PING) == UUID
    assert check_uuid("https://hc-ping.com/not-a-uuid") is None


async def test_heartbeat_pings_ok_or_fail_and_respects_pause(
    logged_in_creds: Credentials, respx_mock: respx.MockRouter
) -> None:
    ok = respx_mock.post(PING).mock(return_value=httpx.Response(200))
    fail = respx_mock.post(f"{PING}/fail").mock(return_value=httpx.Response(200))
    settings = replace(AppSettings(), healthcheck_url=PING)
    async with build_app(logged_in_creds, settings=settings) as app:
        outcome = JobOutcome()
        await heartbeat_job(app, 1, outcome, NullNotifier(), desktop_running=lambda: True)
        assert outcome.detail["pinged"] and ok.called and not fail.called

        closed = JobOutcome()
        await heartbeat_job(app, 2, closed, NullNotifier(), desktop_running=lambda: False)
        assert closed.status == "warn" and fail.called
        assert b"Claude Desktop is not running" in fail.calls.last.request.content

        pause(app.state, None, "trip")
        paused = await run_job("heartbeat", app)
        assert paused.status == "skipped_paused"
        assert ok.call_count == 1


async def test_healthchecks_pause_needs_an_api_key(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"https://healthchecks.io/api/v3/checks/{UUID}/pause").mock(
        return_value=httpx.Response(200)
    )
    async with httpx.AsyncClient() as http:
        assert await Healthchecks(http, PING).pause() is False
        assert await Healthchecks(http, PING, api_key="rw-key").pause() is True
    assert route.calls.last.request.headers["X-Api-Key"] == "rw-key"


async def test_token_reminder_fires_once_a_day_near_expiry(logged_in_creds: Credentials) -> None:
    class Delivering(NullNotifier):
        async def send(
            self, title: str, message: str, *, priority: str = "default", tags: tuple[str, ...] = ()
        ) -> bool:
            await super().send(title, message, priority=priority, tags=tags)
            return True

    notifier = Delivering()
    async with build_app(logged_in_creds) as app:
        record_user_token_expiry(app.state, datetime.now(UTC) + timedelta(days=30))
        far = JobOutcome()
        await remind_token_expiry(app, far, notifier)
        assert notifier.sent == [] and far.detail["user_token_days_left"] in (29, 30)

        record_user_token_expiry(app.state, datetime.now(UTC) + timedelta(days=3, hours=1))
        await remind_token_expiry(app, JobOutcome(), notifier)
        await remind_token_expiry(app, JobOutcome(), notifier)  # same day: no second alert
        assert len(notifier.sent) == 1 and "expires in 3 day(s)" in notifier.sent[0][0]

        record_user_token_expiry(app.state, datetime.now(UTC) - timedelta(days=1))
        await remind_token_expiry(app, JobOutcome(), notifier)
        assert notifier.sent[-1][0] == "Apple Music login expired" and notifier.sent[-1][2] == "high"


def test_logs_are_json_and_redacted(tmp_path: Path) -> None:
    path = setup_logging(tmp_path)
    jwt = make_jwt(9_999_999_999)
    logging.getLogger("apple_music_mcp.test").warning("request failed with %s", jwt)
    for handler in logging.getLogger("apple_music_mcp").handlers:
        handler.flush()
    entry = json.loads(path.read_text().strip().splitlines()[-1])
    assert entry["level"] == "WARNING" and jwt not in entry["msg"] and "<redacted-jwt>" in entry["msg"]
    assert setup_logging(tmp_path) == path  # idempotent
    logger = logging.getLogger("apple_music_mcp")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
