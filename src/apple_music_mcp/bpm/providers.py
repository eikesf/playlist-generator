"""BPM providers. Each one searches a public database by artist + title and returns a tempo.

- ``GetSongBpmProvider``: getsongbpm.com API (free key, ~3000 req/h). Search with
  ``type=both&lookup=song:<title> artist:<artist>``; results carry ``tempo``.
- ``DeezerProvider``: public Deezer API (no key). Search, then ``/track/{id}`` for ``bpm``
  (0 means unknown).

Matches are verified with the same fuzzy scoring the resolver uses, so a tempo is only taken
from the right song.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ..apple.http import BROWSER_UA, RetryPolicy, Sleep, safe_json, send_with_retry
from ..models import Song
from ..resolver import MATCH_THRESHOLD, TrackRequest, score

GETSONGBPM_BASE = "https://api.getsong.co"
DEEZER_BASE = "https://api.deezer.com"
MIN_BPM, MAX_BPM = 40.0, 250.0


@dataclass
class BpmMatch:
    bpm: float
    source: str
    matched: str
    confidence: float


class BpmProvider(Protocol):
    name: str

    async def lookup(self, artist: str, title: str) -> BpmMatch | None: ...


def _valid(value: Any) -> float | None:
    try:
        bpm = float(value)
    except (TypeError, ValueError):
        return None
    return bpm if MIN_BPM <= bpm <= MAX_BPM else None


def _best(artist: str, title: str, candidates: list[tuple[Any, str, str]]) -> tuple[Any, float, str] | None:
    """Pick the candidate (payload, cand_title, cand_artist) that matches artist/title best."""
    request = TrackRequest(artist=artist, title=title, raw=f"{artist} - {title}")
    best: tuple[Any, float, str] | None = None
    for payload, cand_title, cand_artist in candidates:
        s, _ = score(request, Song(id="x", name=cand_title, artist=cand_artist))
        if s >= MATCH_THRESHOLD and (best is None or s > best[1]):
            best = (payload, s, f"{cand_artist} - {cand_title}")
    return best


class _HttpProvider:
    def __init__(
        self, http: httpx.AsyncClient, retry: RetryPolicy | None = None, sleep: Sleep = asyncio.sleep
    ):
        self._http = http
        self._retry = retry or RetryPolicy()
        self._sleep = sleep

    async def _get(self, url: str, params: dict[str, Any]) -> Any:
        async def send() -> httpx.Response:
            return await self._http.get(url, params=params, headers={"User-Agent": BROWSER_UA})

        resp = await send_with_retry(send, self._retry, self._sleep)
        if resp.status_code >= 400:
            return None
        return safe_json(resp)


class GetSongBpmProvider(_HttpProvider):
    name = "getsongbpm"

    def __init__(self, http: httpx.AsyncClient, api_key: str, **kwargs: Any):
        super().__init__(http, **kwargs)
        self._key = api_key

    async def lookup(self, artist: str, title: str) -> BpmMatch | None:
        data = await self._get(
            f"{GETSONGBPM_BASE}/search/",
            {"api_key": self._key, "type": "both", "lookup": f"song:{title} artist:{artist}", "limit": 5},
        )
        results = (data or {}).get("search") if isinstance(data, dict) else None
        if not isinstance(results, list):  # {"search": {"error": "no result"}}
            return None
        candidates = [
            (r, r.get("title", ""), (r.get("artist") or {}).get("name", ""))
            for r in results
            if isinstance(r, dict)
        ]
        best = _best(artist, title, candidates)
        if not best:
            return None
        payload, confidence, matched = best
        bpm = _valid(payload.get("tempo"))
        if bpm is None and payload.get("id"):
            detail = await self._get(f"{GETSONGBPM_BASE}/song/", {"api_key": self._key, "id": payload["id"]})
            bpm = (
                _valid(((detail or {}).get("song") or {}).get("tempo")) if isinstance(detail, dict) else None
            )
        return BpmMatch(bpm, self.name, matched, confidence) if bpm else None


class DeezerProvider(_HttpProvider):
    name = "deezer"

    async def lookup(self, artist: str, title: str) -> BpmMatch | None:
        # Plain query: the advanced `artist:"…" track:"…"` syntax returns no results (live, Nov 2026).
        # `_best` does the artist/title matching instead.
        data = await self._get(f"{DEEZER_BASE}/search", {"q": f"{artist} {title}", "limit": 5})
        if not isinstance(data, dict) or "error" in data:
            return None
        candidates = [
            (r, r.get("title", ""), (r.get("artist") or {}).get("name", ""))
            for r in data.get("data") or []
            if isinstance(r, dict) and r.get("id")
        ]
        best = _best(artist, title, candidates)
        if not best:
            return None
        payload, confidence, matched = best
        detail = await self._get(f"{DEEZER_BASE}/track/{payload['id']}", {})
        if not isinstance(detail, dict) or "error" in detail:
            return None
        bpm = _valid(detail.get("bpm"))  # 0 = not analysed
        return BpmMatch(bpm, self.name, matched, confidence) if bpm else None
