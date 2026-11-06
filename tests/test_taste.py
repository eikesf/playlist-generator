from __future__ import annotations

from pathlib import Path

from mcp import Client
from mcp.server.mcpserver import MCPServer

from apple_music_mcp.config import Credentials
from apple_music_mcp.container import AppContainer
from apple_music_mcp.server import build_server

from .conftest import build_app
from .fake_apple import FakeAppleMusic


async def test_taste_profile_combines_library_recent_and_heavy_rotation(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    profile = await app.taste.profile()
    assert profile.library_ids == {"1001", "2001", "7001"}
    assert [s.id for s in profile.recent] == ["1002"]
    assert profile.heavy_rotation_artists == ["Amelie Lens"]
    top = [a["artist"] for a in profile.top_artists(3)]
    assert top[:2] == ["Amelie Lens", "Charlotte de Witte"]  # heavy rotation and recent weigh more
    assert profile.is_known("1002") and profile.is_known("2001") and not profile.is_known("2002")


async def test_taste_profile_is_cached(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    await app.taste.profile()
    calls = len(fake_apple.calls)
    await app.taste.profile()
    assert len(fake_apple.calls) == calls
    await app.taste.profile(refresh=True)
    assert len(fake_apple.calls) > calls


async def test_heavy_rotation_failure_is_tolerated(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    fake_apple.heavy_rotation = False
    profile = await app.taste.profile(refresh=True)
    assert profile.heavy_rotation_artists == [] and profile.library_ids


async def test_ratings(app: AppContainer) -> None:
    assert await app.taste.ratings(["5002", "2001", "1001"]) == {"5002": -1, "2001": 1}
    assert await app.taste.ratings(["1001"]) == {}  # 404 = nothing rated


async def test_similar_artists(app: AppContainer) -> None:
    artist = await app.catalog.find_artist("Charlotte de Witte")
    assert artist is not None
    names = [a.name for a in await app.catalog.similar_artists(artist.id)]
    assert names == ["Amelie Lens", "Boris Brejcha", "Anyma"]


async def test_discovery_skips_known_disliked_and_seed_artists(app: AppContainer) -> None:
    result = await app.discovery.discover(["Charlotte de Witte"], per_artist=2)
    ids = [c["id"] for c in result.candidates]
    assert "2001" not in ids  # in library (known)
    assert "5002" not in ids  # disliked
    assert all(c["artist"] != "Charlotte de Witte" for c in result.candidates)  # seed itself excluded
    assert ids == ["2002", "2003", "5001", "9001", "9002"]
    assert result.skipped_known == 1 and result.skipped_disliked == 1
    assert result.candidates[0]["similar_to"] == "Charlotte de Witte"
    assert result.candidates[0]["line"] == "Amelie Lens - Feel It"


async def test_discovery_reports_unknown_seed_and_respects_exclusions(app: AppContainer) -> None:
    result = await app.discovery.discover(["Nobody Zzz", "Charlotte de Witte"], exclude=["Anyma"])
    assert result.seeds_not_found == ["Nobody Zzz"]
    assert all(c["artist"] != "Anyma" for c in result.candidates)


def _server(fake_apple: FakeAppleMusic, tmp_path: Path, token: str | None = "default") -> MCPServer:
    creds = Credentials(
        media_user_token=fake_apple.user_token if token == "default" else token, storefront="br"
    )
    return build_server(lambda: build_app(creds))


async def test_resolve_reports_familiarity_buckets_and_dislikes(
    fake_apple: FakeAppleMusic, tmp_path: Path
) -> None:
    tracks = [
        "Charlotte de Witte - Doppler",
        "Amelie Lens - Feel It",
        "Boris Brejcha - Gravity (Ann Clue Remix)",
    ]
    async with Client(_server(fake_apple, tmp_path)) as c:
        data = (await c.call_tool("resolve_tracks", {"tracks": tracks})).structured_content
    rows = {r["request"]: r for r in data["tracks"]}
    assert rows["Charlotte de Witte - Doppler"]["familiarity"] == "known_track"
    assert rows["Amelie Lens - Feel It"]["familiarity"] == "known_artist_new_track"
    assert rows["Boris Brejcha - Gravity (Ann Clue Remix)"]["familiarity"] == "new_artist"
    assert rows["Boris Brejcha - Gravity (Ann Clue Remix)"]["disliked"] is True
    summary = data["summary"]
    assert summary["familiarity"] == {"known_track": 1, "known_artist_new_track": 1, "new_artist": 1}
    assert summary["known_tracks_pct"] == 33 and summary["known_artists_pct"] == 67
    assert summary["disliked"] == 1


async def test_resolve_without_login_still_works(fake_apple: FakeAppleMusic, tmp_path: Path) -> None:
    async with Client(_server(fake_apple, tmp_path, token=None)) as c:
        data = (
            await c.call_tool("resolve_tracks", {"tracks": ["Charlotte de Witte - Doppler"]})
        ).structured_content
    assert data["summary"]["matched"] == 1 and "familiarity" not in data["tracks"][0]


async def test_taste_tools_via_mcp(fake_apple: FakeAppleMusic, tmp_path: Path) -> None:
    async with Client(_server(fake_apple, tmp_path)) as c:
        prof = (await c.call_tool("my_taste_profile", {})).structured_content
        sim = (await c.call_tool("similar_artists", {"artist": "Amelie Lens"})).structured_content
        disc = (await c.call_tool("discover_tracks", {"seed_artists": ["Amelie Lens"]})).structured_content
        missing = await c.call_tool("similar_artists", {"artist": "Nobody Zzz"})
    assert prof["library_songs"] == 3 and prof["top_artists"][0]["artist"] == "Amelie Lens"
    assert [a["name"] for a in sim["similar"]] == ["Charlotte de Witte", "Stephan Bodzin"]
    assert {c["artist"] for c in disc["candidates"]} == {"Charlotte de Witte", "Stephan Bodzin"}
    assert "1001" not in [c["id"] for c in disc["candidates"]]  # Doppler is in the library
    assert missing.is_error


async def test_recently_played_pages_by_ten(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    fake_apple.recent_ids = [
        "1001",
        "1002",
        "1003",
        "2001",
        "2002",
        "2003",
        "3001",
        "4001",
        "4002",
        "5001",
        "5002",
        "7001",
        "8001",
        "9001",
        "9002",
    ]
    songs, other = await app.taste.recent_tracks()
    assert [s.id for s in songs] == fake_apple.recent_ids and other == 0
    pages = [c for c in fake_apple.calls if c[1] == "/v1/me/recent/played/tracks"]
    assert len(pages) == 2  # 10 + 5
