from __future__ import annotations

from mcp import Client

from apple_music_mcp.bpm.service import align_octave
from apple_music_mcp.config import Credentials
from apple_music_mcp.server import build_server

from .conftest import build_app


def test_align_octave():
    assert align_octave(64, 128) == 128
    assert align_octave(174, 87) == 87
    assert align_octave(126, 128) == 126
    assert align_octave(126, None) == 126


async def test_deezer_provides_bpm_without_key(app, fake_apple):
    fake_apple.deezer_bpm = {"1001": 132.0}
    match = await app.bpm.lookup_one("1001", "Charlotte de Witte", "Doppler")
    assert match and match.bpm == 132.0 and match.source == "deezer"
    assert app.bpm.available == ["deezer"]


async def test_zero_bpm_means_unknown_and_misses_are_cached(app, fake_apple):
    assert await app.bpm.lookup_one("2002", "Amelie Lens", "Feel It") is None  # bpm 0 in Deezer
    calls = len(fake_apple.external_calls)
    assert await app.bpm.lookup_one("2002", "Amelie Lens", "Feel It") is None
    assert len(fake_apple.external_calls) == calls  # cached miss, no new request


async def test_wrong_song_is_never_used(app, fake_apple):
    fake_apple.deezer_bpm = {"4003": 99.0}  # only the karaoke version has a bpm
    assert await app.bpm.lookup_one("4001", "Stephan Bodzin", "Singularity") is None


async def test_getsongbpm_first_when_key_configured(fake_apple):
    fake_apple.getsongbpm_tempo = {"5001": "125"}
    fake_apple.deezer_bpm = {"5001": 130.0, "1001": 132.0}
    creds = Credentials(media_user_token=fake_apple.user_token, getsongbpm_api_key=fake_apple.getsongbpm_key)
    async with build_app(creds) as a:
        assert a.bpm.available == ["getsongbpm", "deezer"]
        gravity = await a.bpm.lookup_one("5001", "Boris Brejcha", "Gravity")
        doppler = await a.bpm.lookup_one("1001", "Charlotte de Witte", "Doppler")  # not in GetSongBPM
    assert (gravity.bpm, gravity.source) == (125.0, "getsongbpm")
    assert (doppler.bpm, doppler.source) == (132.0, "deezer")


async def test_measured_bpm_replaces_estimates_in_order_tracks(fake_apple, tmp_path):
    fake_apple.deezer_bpm = {"1001": 66.0, "4001": 121.0}  # 66 = half time of 132
    creds = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    server = build_server(lambda: build_app(creds))
    tracks = [
        {"id": "4001", "artist": "Stephan Bodzin", "title": "Singularity", "bpm": 118, "energy": 3},
        {"id": "1001", "artist": "Charlotte de Witte", "title": "Doppler", "bpm": 130, "energy": 9},
        {"id": "2002", "artist": "Amelie Lens", "title": "Feel It", "bpm": 127, "energy": 6},
    ]
    async with Client(server) as c:
        data = (await c.call_tool("order_tracks", {"purpose": "dj_set", "tracks": tracks})).structured_content
        raw = (
            await c.call_tool(
                "order_tracks", {"purpose": "dj_set", "tracks": tracks, "use_measured_bpm": False}
            )
        ).structured_content
        looked = (await c.call_tool("lookup_bpm", {"tracks": tracks})).structured_content
    rows = {r["id"]: r for r in data["tracks"]}
    assert (rows["4001"]["bpm"], rows["4001"]["bpm_source"]) == (121.0, "deezer")
    assert (rows["1001"]["bpm"], rows["1001"]["bpm_source"]) == (132.0, "deezer")  # octave-aligned
    assert (rows["2002"]["bpm"], rows["2002"]["bpm_source"]) == (127, "estimate")
    assert data["measured_bpm"] == 2
    assert all(r["bpm_source"] == "estimate" for r in raw["tracks"])
    assert looked["found"] == 2 and looked["providers"] == ["deezer"]


def test_getsongbpm_key_is_masked_in_status():
    creds = Credentials(getsongbpm_api_key="k" * 32)
    assert "k" * 32 not in str(creds.redacted())
