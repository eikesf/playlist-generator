"""Resolve free-text track requests ("Artist - Title (Remix)") to Apple Music catalog songs.

Claude proposes tracks from its own knowledge; this module is the verification step. Each
request is searched in the catalog and scored with fuzzy matching on title and artist, with
penalties for unwanted versions (live, karaoke, covers, a remix nobody asked for). Only matches
above the threshold are returned as ``matched``; everything else is reported, never guessed.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Protocol

from rapidfuzz import fuzz

from .models import Song, format_duration

MATCH_THRESHOLD = 80.0
LOW_CONFIDENCE_THRESHOLD = 65.0

VERSION_WORDS = {
    "remix",
    "rmx",
    "edit",
    "live",
    "acoustic",
    "instrumental",
    "dub",
    "vip",
    "rework",
    "bootleg",
    "extended",
    "radio",
    "version",
    "mix",
    "remaster",
    "remastered",
    "demo",
    "karaoke",
    "acapella",
    "reprise",
    "sped",
    "slowed",
}
# Versions that change the track and must not be substituted silently.
STRONG_VERSIONS = {
    "remix",
    "rmx",
    "live",
    "acoustic",
    "instrumental",
    "dub",
    "vip",
    "rework",
    "bootleg",
    "karaoke",
    "acapella",
    "demo",
    "reprise",
    "sped",
    "slowed",
}
# Versions that are essentially the same recording.
NEUTRAL_VERSIONS = {"remaster", "remastered", "radio", "edit", "extended", "version", "mix", "original"}
BAD_ARTIST_WORDS = ("karaoke", "tribute", "cover", "made famous", "in the style of", "8-bit", "lullaby")


class SongSearcher(Protocol):
    async def search_songs(self, term: str, limit: int = 10) -> list[Song]: ...


@dataclass
class TrackRequest:
    artist: str
    title: str
    raw: str

    @classmethod
    def parse(cls, item: str | dict[str, Any]) -> TrackRequest:
        if isinstance(item, dict):
            artist = str(item.get("artist") or "").strip()
            title = str(item.get("title") or item.get("name") or "").strip()
            raw = item.get("raw") or (f"{artist} - {title}" if artist else title)
            return cls(artist=artist, title=title, raw=str(raw))
        text = str(item).strip()
        for sep in (" - ", " – ", " — "):
            if sep in text:
                artist, title = text.split(sep, 1)
                return cls(artist=artist.strip(), title=title.strip(), raw=text)
        return cls(artist="", title=text, raw=text)


@dataclass
class Resolution:
    request: TrackRequest
    status: str  # matched | low_confidence | not_found
    song: Song | None = None
    score: float = 0.0
    candidates: list[Song] = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "request": self.request.raw,
            "status": self.status,
            "score": round(self.score, 1),
        }
        if self.song:
            data["song"] = self.song.to_dict()
        if self.note:
            data["note"] = self.note
        if self.status != "matched" and self.candidates:
            data["candidates"] = [
                {"id": c.id, "label": c.label, "album": c.album, "duration": format_duration(c.duration_ms)}
                for c in self.candidates[:3]
            ]
        return data


@dataclass
class ResolveReport:
    resolutions: list[Resolution]
    duplicates: list[dict[str, str]] = field(default_factory=list)

    @property
    def matched(self) -> list[Resolution]:
        return [r for r in self.resolutions if r.status == "matched"]

    @property
    def song_ids(self) -> list[str]:
        return [r.song.id for r in self.matched if r.song]

    @property
    def total_duration_ms(self) -> int:
        return sum(r.song.duration_ms or 0 for r in self.matched if r.song)

    def to_dict(self) -> dict[str, Any]:
        statuses = [r.status for r in self.resolutions]
        return {
            "summary": {
                "requested": len(self.resolutions),
                "matched": statuses.count("matched"),
                "low_confidence": statuses.count("low_confidence"),
                "not_found": statuses.count("not_found"),
                "duplicates_removed": len(self.duplicates),
                "total_duration": format_duration(self.total_duration_ms),
                "total_duration_ms": self.total_duration_ms,
            },
            "song_ids_in_order": self.song_ids,
            "tracks": [r.to_dict() for r in self.resolutions],
            "duplicates": self.duplicates,
        }


# --------------------------------------------------------------------------- normalization
_FEAT_RE = re.compile(r"[\(\[]\s*(feat\.?|ft\.?|featuring|with)\s+[^\)\]]*[\)\]]", re.I)
_FEAT_TAIL_RE = re.compile(r"\s+(feat\.?|ft\.?|featuring)\s+.*$", re.I)
_BRACKET_RE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_DASH_SUFFIX_RE = re.compile(r"\s+-\s+(.*)$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize(text: str) -> str:
    text = strip_accents(text).lower().replace("&", " and ").replace("$", "s")
    return _NON_ALNUM_RE.sub(" ", text).strip()


def split_title(title: str) -> tuple[str, set[str], str]:
    """Return (core title, version words, version descriptor text) for a song title."""
    text = _FEAT_RE.sub("", title)
    text = _FEAT_TAIL_RE.sub("", text)
    descriptors: list[str] = [m.group(1) for m in _BRACKET_RE.finditer(text)]
    text = _BRACKET_RE.sub("", text)
    dash = _DASH_SUFFIX_RE.search(text)
    if dash and set(normalize(dash.group(1)).split()) & VERSION_WORDS:
        descriptors.append(dash.group(1))
        text = text[: dash.start()]
    words: set[str] = set()
    for d in descriptors:
        words |= set(normalize(d).split()) & (VERSION_WORDS | {"original"})
    return normalize(text), words, normalize(" ".join(descriptors))


def artist_names(artist: str) -> list[str]:
    parts = re.split(r",|&| and | x | feat\.? | ft\.? | featuring | vs\.? ", strip_accents(artist).lower())
    return [normalize(p) for p in parts if normalize(p)]


# --------------------------------------------------------------------------- scoring
def score(request: TrackRequest, song: Song) -> tuple[float, str | None]:
    req_core, req_versions, req_desc = split_title(request.title)
    cand_core, cand_versions, cand_desc = split_title(song.name)
    title_score = max(fuzz.ratio(req_core, cand_core), fuzz.token_sort_ratio(req_core, cand_core))
    if req_core and cand_core and (req_core in cand_core or cand_core in req_core):
        title_score = max(title_score, 90.0 if min(len(req_core), len(cand_core)) > 3 else title_score)

    if request.artist:
        req_artists = artist_names(request.artist)
        cand_artists = artist_names(song.artist)
        primary = max((fuzz.ratio(req_artists[0], c) for c in cand_artists), default=0) if req_artists else 0
        overall = fuzz.token_set_ratio(normalize(request.artist), normalize(song.artist))
        artist_score = max(primary, overall)
    else:
        artist_score = 75.0

    total = 0.6 * title_score + 0.4 * artist_score
    note = None
    req_strong = req_versions & STRONG_VERSIONS
    cand_strong = cand_versions & STRONG_VERSIONS
    if cand_strong - req_strong:
        total -= 25
        note = f"catalog version differs ({cand_desc or ', '.join(sorted(cand_strong))})"
    elif req_strong - cand_strong:
        total -= 25
        note = f"requested version not found ({req_desc})"
    elif req_strong and req_desc and cand_desc:
        # Both are remixes: make sure it is the same remixer.
        req_who = " ".join(w for w in req_desc.split() if w not in VERSION_WORDS)
        cand_who = " ".join(w for w in cand_desc.split() if w not in VERSION_WORDS)
        remix_sim = fuzz.token_set_ratio(req_who, cand_who) if req_who and cand_who else 100
        if remix_sim < 70:
            total -= 25
            note = f"different remix ({cand_desc})"
    if any(w in song.artist.lower() for w in BAD_ARTIST_WORDS):
        total -= 40
        note = "karaoke/cover artist"
    if title_score < 60 or artist_score < 55:
        total = min(total, LOW_CONFIDENCE_THRESHOLD - 1)
    return max(0.0, min(100.0, total)), note


def best_match(request: TrackRequest, candidates: list[Song]) -> tuple[Song | None, float, str | None]:
    best: tuple[Song | None, float, str | None] = (None, 0.0, None)
    for cand in candidates:
        s, note = score(request, cand)
        if s > best[1]:
            best = (cand, s, note)
    return best


async def resolve_one(searcher: SongSearcher, request: TrackRequest, limit: int = 10) -> Resolution:
    query = f"{request.artist} {request.title}".strip()
    candidates = await searcher.search_songs(query, limit=limit)
    song, s, note = best_match(request, candidates)
    if s < MATCH_THRESHOLD and request.artist:
        # Some catalog titles carry long subtitles; retry with the core title only.
        core, _, _ = split_title(request.title)
        retry_query = f"{request.artist} {core}".strip()
        if retry_query.lower() != query.lower():
            more = await searcher.search_songs(retry_query, limit=limit)
            seen = {c.id for c in candidates}
            candidates += [c for c in more if c.id not in seen]
            song, s, note = best_match(request, candidates)
    if song and s >= MATCH_THRESHOLD:
        status = "matched"
    elif song and s >= LOW_CONFIDENCE_THRESHOLD:
        status = "low_confidence"
    else:
        status = "not_found"
    ranked = sorted(candidates, key=lambda c: score(request, c)[0], reverse=True)
    return Resolution(
        request=request,
        status=status,
        song=song if status != "not_found" else None,
        score=s,
        candidates=ranked,
        note=note,
    )


async def resolve_tracks(
    searcher: SongSearcher, items: list[str | dict[str, Any]], concurrency: int = 4
) -> ResolveReport:
    requests = [TrackRequest.parse(i) for i in items]
    sem = asyncio.Semaphore(concurrency)

    async def run(req: TrackRequest) -> Resolution:
        async with sem:
            return await resolve_one(searcher, req)

    resolutions = list(await asyncio.gather(*(run(r) for r in requests)))
    return dedupe(resolutions)


def dedupe(resolutions: list[Resolution]) -> ResolveReport:
    """Drop repeated songs (same id, or same artist + core title such as radio edit vs original)."""
    seen_ids: dict[str, str] = {}
    seen_keys: dict[tuple[str, str], str] = {}
    duplicates: list[dict[str, str]] = []
    kept: list[Resolution] = []
    for r in resolutions:
        if r.status == "matched" and r.song:
            primary = (artist_names(r.song.artist) or [normalize(r.song.artist)])[0]
            core, versions, _ = split_title(r.song.name)
            key = (primary, core + "|" + ",".join(sorted(versions & STRONG_VERSIONS)))
            first = seen_ids.get(r.song.id) or seen_keys.get(key)
            if first:
                duplicates.append({"request": r.request.raw, "duplicate_of": first})
                continue
            seen_ids[r.song.id] = r.request.raw
            seen_keys[key] = r.request.raw
        kept.append(r)
    return ResolveReport(resolutions=kept, duplicates=duplicates)
