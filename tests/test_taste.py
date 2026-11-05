from __future__ import annotations

from mcp import Client

from apple_music_mcp.config import Credentials
from apple_music_mcp.server import build_server

from .conftest import build_app


async def test_taste_profile_combines_library_recent_and_heavy_rotation(app, fake_apple):
    profile = await app.taste.profile()
    assert profile.library_ids == {"1001", "2001", "7001"}
    assert [s.id for s in profile.recent] == ["1002"]
    assert profile.heavy_rotation_artists == ["Amelie Lens"]
    top = [a["artist"] for a in profile.top_artists(3)]
    assert top[:2] == ["Amelie Lens", "Charlotte de Witte"]  # heavy rotation and recent weigh more
    assert profile.is_known("1002") and profile.is_known("2001") and not profile.is_known("2002")


async def test_taste_profile_is_cached(app, fake_apple):
    await app.taste.profile()
    calls = len(fake_apple.calls)
    await app.taste.profile()
    assert len(fake_apple.calls) == calls
    await app.taste.profile(refresh=True)
    assert len(fake_apple.calls) > calls


async def test_heavy_rotation_failure_is_tolerated(app, fake_apple):
    fake_apple.heavy_rotation = False
    profile = await app.taste.profile(refresh=True)
    assert profile.heavy_rotation_artists == [] and profile.library_ids


async def test_ratings(app):
    assert await app.taste.ratings(["5002", "2001", "1001"]) == {"5002": -1, "2001": 1}
    assert await app.taste.ratings(["1001"]) == {}  # 404 = nothing rated


async def test_similar_artists(app):
    artist = await app.catalog.find_artist("Charlotte de Witte")
    names = [a.name for a in await app.catalog.similar_artists(artist.id)]
    assert names == ["Amelie Lens", "Boris Brejcha", "Anyma"]


async def test_discovery_skips_known_disliked_and_seed_artists(app):
    result = await app.discovery.discover(["Charlotte de Witte"], per_artist=2)
    ids = [c["id"] for c in result.candidates]
    assert "2001" not in ids  # in library (known)
    assert "5002" not in ids  # disliked
    assert all(c["artist"] != "Charlotte de Witte" for c in result.candidates)  # seed itself excluded
    assert ids == ["2002", "2003", "5001", "9001", "9002"]
    assert result.skipped_known == 1 and result.skipped_disliked == 1
    assert result.candidates[0]["similar_to"] == "Charlotte de Witte"
    assert result.candidates[0]["line"] == "Amelie Lens - Feel It"


async def test_discovery_reports_unknown_seed_and_respects_exclusions(app):
    result = await app.discovery.discover(["Nobody Zzz", "Charlotte de Witte"], exclude=["Anyma"])
    assert result.seeds_not_found == ["Nobody Zzz"]
    assert all(c["artist"] != "Anyma" for c in result.candidates)


def _server(fake_apple, tmp_path, token="default"):
    creds = Credentials(
        media_user_token=fake_apple.user_token if token == "default" else token, storefront="br"
    )
    return build_server(lambda: build_app(creds), export_dir=tmp_path)


async def test_resolve_reports_real_known_new_ratio_and_dislikes(fake_apple, tmp_path):
    tracks = [
        "Charlotte de Witte - Doppler",
        "Amelie Lens - Feel It",
        "Boris Brejcha - Gravity (Ann Clue Remix)",
    ]
    async with Client(_server(fake_apple, tmp_path)) as c:
        data = (await c.call_tool("resolve_tracks", {"tracks": tracks})).structured_content
    rows = {r["request"]: r for r in data["tracks"]}
    assert rows["Charlotte de Witte - Doppler"]["familiarity"] == "known"
    assert rows["Amelie Lens - Feel It"]["familiarity"] == "new"
    assert rows["Boris Brejcha - Gravity (Ann Clue Remix)"]["disliked"] is True
    assert data["summary"]["known"] == 1 and data["summary"]["new"] == 2
    assert data["summary"]["known_ratio"] == 0.33 and data["summary"]["disliked"] == 1


async def test_resolve_without_login_still_works(fake_apple, tmp_path):
    async with Client(_server(fake_apple, tmp_path, token=None)) as c:
        data = (
            await c.call_tool("resolve_tracks", {"tracks": ["Charlotte de Witte - Doppler"]})
        ).structured_content
    assert data["summary"]["matched"] == 1 and "familiarity" not in data["tracks"][0]


async def test_taste_tools_via_mcp(fake_apple, tmp_path):
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
