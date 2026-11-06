"""Shared HTTP concerns: browser identity, retry policy and JSON helpers (one place, DRY)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retries for transient failures (guide §4.1).

    Up to 3 retries waiting 1 s, 2 s, 4 s; a 429 backs off twice as long; ``Retry-After`` is honoured
    up to ``max_delay``. ``max_total`` caps the whole wait so a tool call never sleeps long enough to hit
    the MCP client's timeout: when the next wait would exceed it, the last response is returned as is.
    Connection errors and timeouts are retried the same way and re-raised at the end.
    """

    max_retries: int = 3
    base_delay: float = 1.0
    rate_limit_factor: float = 2.0
    max_delay: float = 8.0
    max_total: float = 15.0
    statuses: frozenset[int] = field(default_factory=lambda: frozenset({429, 500, 502, 503, 504}))

    def delay(self, resp: httpx.Response | None, attempt: int) -> float:
        """Wait before retry number ``attempt`` (1-based). ``resp`` is None after a network error."""
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    return max(0.0, min(float(retry_after), self.max_delay))
                except ValueError:
                    pass
        wait: float = self.base_delay * 2.0 ** (attempt - 1)
        if resp is not None and resp.status_code == 429:
            wait *= self.rate_limit_factor
        return min(wait, self.max_delay)


async def send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    policy: RetryPolicy,
    sleep: Sleep = asyncio.sleep,
) -> httpx.Response:
    attempt = 0
    waited = 0.0
    while True:
        try:
            resp: httpx.Response | None = await send()
            error: httpx.TransportError | None = None
        except httpx.TransportError as exc:
            resp, error = None, exc
        retryable = error is not None or (resp is not None and resp.status_code in policy.statuses)
        if retryable and attempt < policy.max_retries:
            wait = policy.delay(resp, attempt + 1)
            if waited + wait <= policy.max_total:
                attempt += 1
                waited += wait
                await sleep(wait)
                continue
        if error is not None:
            raise error
        assert resp is not None
        return resp


class RateLimiter:
    """At most ``rate`` requests per second and ``concurrency`` in flight (guide §4.1: ~5/s, 2-3 at once).

    One limiter is shared by every call to the same service, so a burst of tool calls is spread out
    instead of hitting Apple all at once.
    """

    def __init__(
        self,
        rate: float = 5.0,
        concurrency: int = 3,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ):
        self.interval = 1.0 / rate if rate > 0 else 0.0
        self._semaphore = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()
        self._next_slot = 0.0
        self._clock = clock
        self._sleep = sleep

    async def __aenter__(self) -> RateLimiter:
        await self._semaphore.acquire()
        try:
            async with self._lock:
                now = self._clock()
                wait = self._next_slot - now
                self._next_slot = max(now, self._next_slot) + self.interval
            if wait > 0:
                await self._sleep(wait)
        except BaseException:
            self._semaphore.release()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._semaphore.release()


def safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:500]
