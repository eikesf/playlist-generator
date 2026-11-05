"""A stateful, in-memory imitation of the Apple Music web endpoints, mounted with respx.

It mirrors the shapes documented by the unofficial clients (amp-api JSON:API resources) so the
whole flow (token scrape → search → resolve → create playlist → add tracks) can be exercised
without network access. It also enforces auth the way Apple does: a wrong developer token gives
401, a missing/invalid media-user-token on /v1/me gives 401/403.
"""

from __future__ import annotations

import itertools
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import httpx
import respx

from apple_music_mcp.resolver import normalize

from .conftest import make_jwt

AMP = "https://amp-api.music.apple.com"


@dataclass
class FakeSong:
    id: str
    name: str
    artist: str
    album: str
    duration_ms: int
    genres: tuple[str, ...] = ("Techno", "Electronic", "Music")
    explicit: bool = False

    def resource(self, storefront: str) -> dict:
        return {
            "id": self.id,
            "type": "songs",
            "href": f"/v1/catalog/{storefront}/songs/{self.id}",
            "attributes": {
                "name": self.name,
                "artistName": self.artist,
                "albumName": self.album,
                "durationInMillis": self.duration_ms,
                "genreNames": list(self.genres),
                "releaseDate": "2021-05-14",
                "url": f"https://music.apple.com/{storefront}/song/{self.id}",
                "isrc": f"TEST{self.id}",
                **({"contentRating": "explicit"} if self.explicit else {}),
            },
        }

    def itunes(self, storefront: str) -> dict:
        return {
            "wrapperType": "track",
            "kind": "song",
            "trackId": int(self.id),
            "trackName": self.name,
            "artistName": self.artist,
            "collectionName": self.album,
            "trackTimeMillis": self.duration_ms,
            "primaryGenreName": self.genres[0],
            "releaseDate": "2021-05-14T12:00:00Z",
            "trackViewUrl": f"https://music.apple.com/{storefront}/album/x/1?i={self.id}",
        }


DEFAULT_CATALOG = [
    FakeSong("1001", "Doppler", "Charlotte de Witte", "Doppler - EP", 372000),
    FakeSong("1002", "Selected", "Charlotte de Witte", "Selected - EP", 345000),
    FakeSong("1003", "Formula", "Charlotte de Witte", "Formula - EP", 401000),
    FakeSong("1004", "Doppler (Radio Edit)", "Charlotte de Witte", "Doppler - Single", 201000),
    FakeSong("2001", "Hypnotized", "Amelie Lens", "Hypnotized - EP", 389000),
    FakeSong("2002", "Feel It", "Amelie Lens", "Hypnotized - EP", 352000),
    FakeSong("2003", "In My Mind", "Amelie Lens", "Stay with Me - EP", 333000),
    FakeSong("3001", "Your Mind", "Adam Beyer & Bart Skils", "Your Mind - Single", 418000),
    FakeSong("3002", "Your Mind (Live at Awakenings)", "Adam Beyer", "Live", 455000),
    FakeSong("4001", "Singularity", "Stephan Bodzin", "Powers of Ten", 467000, ("Electronic", "Music")),
    FakeSong("4002", "Strand", "Stephan Bodzin", "Powers of Ten", 432000, ("Electronic", "Music")),
    FakeSong("4003", "Singularity (Karaoke Version)", "Karaoke Hits Band", "Karaoke Vol 9", 467000),
    FakeSong("5001", "Gravity", "Boris Brejcha", "Gravity", 498000),
    FakeSong("5002", "Gravity (Ann Clue Remix)", "Boris Brejcha", "Gravity Remixes", 444000),
    FakeSong(
        "6001",
        "Age of Love (Charlotte de Witte & Enrico Sangiuliano Remix)",
        "Age of Love",
        "Age of Love Remixes",
        385000,
    ),
    FakeSong("7001", "Opus", "Eric Prydz", "Opus", 543000, ("Dance", "Electronic", "Music")),
    FakeSong("8001", "Café del Mar (Michael Woods Remix)", "Energy 52", "Café del Mar", 412000),
]

ARTISTS = {
    "a1": ("Charlotte de Witte", ["1001", "1002", "1003", "1004"]),
    "a2": ("Amelie Lens", ["2001", "2002", "2003"]),
    "a3": ("Stephan Bodzin", ["4001", "4002"]),
    "a4": ("Boris Brejcha", ["5001", "5002"]),
}


@dataclass
class FakeAppleMusic:
    storefront: str = "br"
    user_token: str = "valid-media-user-token-0123456789"
    catalog: list[FakeSong] = field(default_factory=lambda: list(DEFAULT_CATALOG))
    dev_token: str = field(default_factory=lambda: make_jwt(time.time() + 90 * 86400))
    amp_available: bool = True
    playlists: dict[str, dict] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    fail_next: list[int] = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    # ------------------------------------------------------------------ helpers
    @property
    def songs_by_id(self) -> dict[str, FakeSong]:
        return {s.id: s for s in self.catalog}

    def search(self, term: str, limit: int) -> list[FakeSong]:
        words = set(normalize(term).split())
        scored = []
        for s in self.catalog:
            primary = set(normalize(f"{s.artist} {s.name}").split())
            hay = primary | set(normalize(s.album).split())
            overlap = len(words & hay)
            if words and overlap / len(words) >= 0.5:
                scored.append((len(words & primary) * 10 + overlap, -len(s.name), s))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [s for _, _, s in scored[:limit]]

    def install(self, router: respx.Router) -> FakeAppleMusic:
        router.get(url__regex=r"https://music\.apple\.com/(us/browse)?$").mock(side_effect=self._html)
        router.get(url__regex=r"https://music\.apple\.com/assets/.*\.js$").mock(side_effect=self._bundle)
        router.get(url__startswith="https://itunes.apple.com/search").mock(side_effect=self._itunes)
        router.route(url__startswith=AMP).mock(side_effect=self._amp)
        return self

    # ---------------------------------------------------------------- web player
    def _html(self, request: httpx.Request) -> httpx.Response:
        html = (
            '<html><head><script type="module" crossorigin src="/assets/index-9f8e7d.js"></script>'
            '<script nomodule src="/assets/index-legacy-1a2b3c.js"></script></head></html>'
        )
        return httpx.Response(200, text=html)

    def _bundle(self, request: httpx.Request) -> httpx.Response:
        if "legacy" in request.url.path:
            return httpx.Response(200, text="console.log('legacy')")
        expired = make_jwt(time.time() - 3600)
        return httpx.Response(200, text=f'const a="{expired}";const devToken="{self.dev_token}";')

    def _itunes(self, request: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(request.url)).query)
        term = q.get("term", [""])[0]
        limit = int(q.get("limit", ["10"])[0])
        results = [s.itunes(self.storefront) for s in self.search(term, limit)]
        return httpx.Response(200, json={"resultCount": len(results), "results": results})

    # ------------------------------------------------------------------- amp-api
    def _amp(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if self.fail_next:
            status = self.fail_next.pop(0)
            return httpx.Response(
                status, headers={"Retry-After": "0"}, json={"errors": [{"status": str(status)}]}
            )
        if not self.amp_available:
            return httpx.Response(503, json={"errors": [{"status": "503"}]})
        if request.headers.get("Authorization") != f"Bearer {self.dev_token}":
            return httpx.Response(401, json={"errors": [{"status": "401", "title": "Unauthorized"}]})
        if path.startswith("/v1/me"):
            mut = request.headers.get("media-user-token")
            if not mut:
                return httpx.Response(401, json={"errors": [{"status": "401"}]})
            if mut != self.user_token:
                return httpx.Response(403, json={"errors": [{"status": "403", "title": "Forbidden"}]})
            return self._me(request, path)
        return self._catalog(request, path)

    def _catalog(self, request: httpx.Request, path: str) -> httpx.Response:
        q = parse_qs(urlparse(str(request.url)).query)
        sf = self.storefront
        if path == f"/v1/catalog/{sf}/search":
            term = q.get("term", [""])[0]
            limit = int(q.get("limit", ["5"])[0])
            if limit > 25:
                return httpx.Response(400, json={"errors": [{"title": "Invalid Parameter Value"}]})
            types = q.get("types", ["songs"])[0]
            results: dict = {}
            if "songs" in types:
                found = self.search(term, limit)
                if found:
                    results["songs"] = {"data": [s.resource(sf) for s in found]}
            if "artists" in types:
                words = set(normalize(term).split())
                arts = [
                    {"id": aid, "type": "artists", "attributes": {"name": name, "genreNames": ["Techno"]}}
                    for aid, (name, _) in ARTISTS.items()
                    if words & set(normalize(name).split())
                ]
                if arts:
                    results["artists"] = {"data": arts[:limit]}
            return httpx.Response(200, json={"results": results})
        m = re.fullmatch(rf"/v1/catalog/{sf}/artists/(\w+)/view/top-songs", path)
        if m:
            if m.group(1) not in ARTISTS:
                return httpx.Response(404, json={"errors": [{"status": "404"}]})
            ids = ARTISTS[m.group(1)][1]
            return httpx.Response(200, json={"data": [self.songs_by_id[i].resource(sf) for i in ids]})
        if path == f"/v1/catalog/{sf}/songs":
            ids = q.get("ids", [""])[0].split(",")
            return httpx.Response(
                200, json={"data": [self.songs_by_id[i].resource(sf) for i in ids if i in self.songs_by_id]}
            )
        return httpx.Response(404, json={"errors": [{"status": "404"}]})

    def _me(self, request: httpx.Request, path: str) -> httpx.Response:
        if path == "/v1/me/storefront":
            return httpx.Response(200, json={"data": [{"id": self.storefront, "type": "storefronts"}]})
        if path == "/v1/me/library/playlists" and request.method == "GET":
            data = [self._playlist_resource(pid) for pid in self.playlists]
            return httpx.Response(200, json={"data": data})
        if path == "/v1/me/library/playlists" and request.method == "POST":
            body = json.loads(request.content or b"{}")
            attrs = body.get("attributes") or {}
            if not attrs.get("name"):
                return httpx.Response(400, json={"errors": [{"title": "name required"}]})
            pid = f"p.FAKE{next(self._ids):04d}"
            tracks = [
                t["id"] for t in ((body.get("relationships") or {}).get("tracks") or {}).get("data", [])
            ]
            self.playlists[pid] = {
                "name": attrs["name"],
                "description": attrs.get("description"),
                "tracks": tracks,
            }
            return httpx.Response(201, json={"data": [self._playlist_resource(pid)]})
        m = re.fullmatch(r"/v1/me/library/playlists/([\w.]+)/tracks", path)
        if m and request.method == "POST":
            pid = m.group(1)
            if pid not in self.playlists:
                return httpx.Response(404, json={"errors": [{"status": "404"}]})
            body = json.loads(request.content or b"{}")
            data = body.get("data") or []
            if len(data) > 100:
                return httpx.Response(400, json={"errors": [{"title": "too many tracks in one request"}]})
            for t in data:
                if t.get("type") != "songs" or t.get("id") not in self.songs_by_id:
                    return httpx.Response(400, json={"errors": [{"title": f"invalid track {t}"}]})
            self.playlists[pid]["tracks"].extend(t["id"] for t in data)
            return httpx.Response(204)
        m = re.fullmatch(r"/v1/me/library/playlists/([\w.]+)", path)
        if m and request.method == "DELETE":
            if self.playlists.pop(m.group(1), None) is None:
                return httpx.Response(404, json={"errors": [{"status": "404"}]})
            return httpx.Response(204)
        return httpx.Response(404, json={"errors": [{"status": "404"}]})

    def _playlist_resource(self, pid: str) -> dict:
        p = self.playlists[pid]
        return {
            "id": pid,
            "type": "library-playlists",
            "attributes": {
                "name": p["name"],
                "description": {"standard": p["description"]} if p["description"] else None,
                "canEdit": True,
                "dateAdded": "2026-11-05T03:00:00Z",
            },
        }
