"""Shared HTTP concerns: browser identity, retry policy and JSON helpers (one place, DRY)."""

from __future__ import annotations

import asyncio
import random
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
    """Retry transient failures (rate limit / 5xx) with Retry-After or jittered backoff."""

    max_retries: int = 3
    max_delay: float = 10.0
    statuses: frozenset[int] = field(default_factory=lambda: frozenset({429, 500, 502, 503, 504}))

    def delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), self.max_delay)
            except ValueError:
                pass
        return min(self.max_delay, random.uniform(0, 0.5 * 2**attempt))


async def send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    policy: RetryPolicy,
    sleep: Sleep = asyncio.sleep,
) -> httpx.Response:
    attempt = 0
    while True:
        resp = await send()
        if resp.status_code in policy.statuses and attempt < policy.max_retries:
            attempt += 1
            await sleep(policy.delay(resp, attempt))
            continue
        return resp


def safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:500]
