"""Guide §4.1: rate limit, bounded retry, response cache."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from apple_music_mcp.apple.http import RateLimiter, RetryPolicy, send_with_retry
from apple_music_mcp.apple.transport import cache_rule
from apple_music_mcp.db import Database
from apple_music_mcp.db.cache import ResponseCache, cache_key


def test_backoff_is_1_2_4_seconds_and_429_waits_longer():
    policy = RetryPolicy()
    waits = [policy.delay(httpx.Response(503), n) for n in (1, 2, 3)]
    assert waits == [1.0, 2.0, 4.0]
    assert [policy.delay(httpx.Response(429), n) for n in (1, 2, 3)] == [2.0, 4.0, 8.0]
    assert policy.delay(httpx.Response(429, headers={"Retry-After": "3"}), 1) == 3.0
    assert policy.delay(httpx.Response(429, headers={"Retry-After": "120"}), 1) == policy.max_delay
    assert policy.delay(None, 2) == 2.0  # network error


async def test_total_wait_is_bounded_so_tool_calls_do_not_time_out():
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    async def always_rate_limited() -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "8"})

    resp = await send_with_retry(always_rate_limited, RetryPolicy(), sleep)
    assert resp.status_code == 429
    assert sum(slept) <= RetryPolicy().max_total
    assert slept == [8.0]  # a second 8 s wait would pass the 15 s budget


async def test_network_errors_are_retried_then_raised():
    calls = 0

    async def flaky() -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectTimeout("timeout")
        return httpx.Response(200)

    async def no_sleep(_: float) -> None:
        return None

    assert (await send_with_retry(flaky, RetryPolicy(), no_sleep)).status_code == 200

    async def dead() -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        await send_with_retry(dead, RetryPolicy(), no_sleep)


async def test_rate_limiter_spaces_requests_and_caps_concurrency():
    clock = [0.0]
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(round(seconds, 3))
        clock[0] += seconds

    limiter = RateLimiter(rate=5, concurrency=2, clock=lambda: clock[0], sleep=fake_sleep)
    in_flight = 0
    peak = 0

    async def call() -> None:
        nonlocal in_flight, peak
        async with limiter:
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1

    await asyncio.gather(*(call() for _ in range(5)))
    assert peak <= 2
    assert clock[0] == pytest.approx(0.8)  # 5 requests at 5/s: slots 0, .2, .4, .6, .8


def test_cache_rules_never_cover_playlists_or_writes():
    def ns(path: str) -> str | None:
        rule = cache_rule("GET", path)
        return rule.namespace if rule else None

    assert ns("/v1/catalog/br/search") == "catalog"
    assert ns("/v1/me/library/songs") == "library"
    assert ns("/v1/me/ratings/songs") == "ratings"
    assert cache_rule("GET", "/v1/me/library/playlists/p.1/tracks") is None
    assert cache_rule("GET", "/v1/me/storefront") is None
    assert cache_rule("GET", "/v1/me/recent/played/tracks") is None
    assert cache_rule("POST", "/v1/catalog/br/search") is None


def test_response_cache_expires_and_invalidates(tmp_path):
    db = Database.open(tmp_path / "c.db")
    now = [datetime(2026, 11, 6, tzinfo=UTC)]
    cache = ResponseCache(db, clock=lambda: now[0])
    key = cache_key("catalog", "/v1/catalog/br/search", {"term": "x"})
    cache.set(key, {"results": [1]}, timedelta(days=7))
    assert cache.get(key) == {"results": [1]}
    now[0] += timedelta(days=8)
    assert cache.get(key) is None
    lib = cache_key("library", "/v1/me/library/songs", None)
    cache.set(lib, {"data": []}, timedelta(hours=24))
    assert cache.invalidate("library") == 1
    assert cache.get(lib) is None
    db.dispose()


async def test_repeated_catalog_search_is_served_from_cache(app, fake_apple):
    await app.catalog.search_songs("Stephan Bodzin Singularity")
    before = len([c for c in fake_apple.calls if c[1].endswith("/search")])
    await app.catalog.search_songs("Stephan Bodzin Singularity")
    after = len([c for c in fake_apple.calls if c[1].endswith("/search")])
    assert before == after == 1


async def test_playlist_reads_always_hit_apple(app, fake_apple):
    pl = await app.library.create_playlist("Cache check")
    await app.library.add_tracks(pl.id, ["4001"])
    first = await app.library.playlist_tracks(pl.id)
    await app.library.add_tracks(pl.id, ["4002"])
    second = await app.library.playlist_tracks(pl.id)
    assert len(second) == len(first) + 1
