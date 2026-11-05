"""Adding tracks to an existing playlist: each lands where it fits, the rest keeps its order."""

from __future__ import annotations

import random
import time

from mcp import Client

from apple_music_mcp.config import Credentials
from apple_music_mcp.curation.profiles import PROFILES
from apple_music_mcp.curation.sequencer import Sequencer, TrackFeatures, insert_tracks
from apple_music_mcp.server import build_server

from .conftest import build_app

DJ = PROFILES["dj_set"]


def _t(i, artist, bpm, energy, ms=390_000):
    return TrackFeatures(id=i, artist=artist, title=i, bpm=bpm, energy=energy, duration_ms=ms)


def _set() -> list[TrackFeatures]:
    """An existing 10-track set already in journey order."""
    spec = [
        ("a", "Stephan Bodzin", 121, 3),
        ("b", "Tale Of Us", 122, 4),
        ("c", "Mind Against", 124, 5),
        ("d", "Amelie Lens", 126, 6),
        ("e", "Adam Beyer", 128, 7),
        ("f", "Anyma", 130, 8),
        ("g", "Charlotte de Witte", 133, 9.5),
        ("h", "I Hate Models", 136, 10),
        ("i", "Maceo Plex", 128, 7),
        ("j", "Monolink", 122, 4.5),
    ]
    return [_t(*s) for s in spec]


def _is_subsequence(small: list[str], big: list[str]) -> bool:
    it = iter(big)
    return all(x in it for x in small)


def test_existing_order_is_never_changed():
    base = _set()
    rnd = random.Random(5)
    new = [_t(f"n{k}", f"New {k}", rnd.uniform(120, 138), rnd.uniform(2, 10)) for k in range(6)]
    report = insert_tracks(DJ, base, new)
    ids = report["song_ids_in_order"]
    assert len(ids) == 16
    assert _is_subsequence([t.id for t in base], ids)
    assert {p["id"] for p in report["inserted"]} == {t.id for t in new}


def test_calm_track_goes_to_the_opening_and_banger_to_the_peak():
    base = _set()
    calm = _t("calm", "Christian Löffler", 118, 2.5)
    banger = _t("bang", "Enrico Sangiuliano", 135, 9.8)
    report = insert_tracks(DJ, base, [calm, banger])
    placed = {p["id"]: p for p in report["inserted"]}
    assert placed["calm"]["position"] <= 2
    assert placed["calm"]["act"] == "Atmospheric opening"
    assert placed["bang"]["act"] == "Peak"
    assert placed["bang"]["after"] and placed["bang"]["before"]  # sits between two peak tracks


def test_low_energy_track_lands_in_a_low_energy_region_and_pinning_decides_which():
    free = insert_tracks(DJ, _set(), [_t("close", "Ben Böhmer", 120, 4)])["inserted"][0]
    assert free["act"] in {"Atmospheric opening", "Groove / warm-up", "Cooldown"}
    pinned_track = _t("close", "Ben Böhmer", 120, 4)
    pinned_track.act = "Cooldown"
    pinned = insert_tracks(DJ, _set(), [pinned_track])["inserted"][0]
    assert pinned["act"] == "Cooldown" and pinned["position"] >= 10


def test_new_track_avoids_landing_next_to_the_same_artist():
    base = _set()
    report = insert_tracks(DJ, base, [_t("g2", "Charlotte de Witte", 133, 9.5)])
    ids = report["song_ids_in_order"]
    i = ids.index("g2")
    neighbours = {ids[i - 1], ids[i + 1] if i + 1 < len(ids) else None}
    assert "g" not in neighbours


def test_tracks_already_in_the_playlist_are_skipped():
    base = _set()
    report = insert_tracks(DJ, base, [_t("d", "Amelie Lens", 126, 6)])
    assert report["inserted"] == []
    assert report["song_ids_in_order"] == [t.id for t in base]
    assert any("Already in the playlist" in w for w in report["warnings"])


def test_inserting_into_an_empty_playlist_works():
    report = insert_tracks(DJ, [], [_t("x", "A", 125, 5), _t("y", "B", 122, 3)])
    assert sorted(report["song_ids_in_order"]) == ["x", "y"]


def test_inserting_5_tracks_into_75_is_fast():
    rnd = random.Random(9)
    pool = [_t(str(i), f"A{i % 25}", rnd.uniform(118, 140), rnd.uniform(2, 10)) for i in range(75)]
    base = Sequencer(DJ).order(pool)
    new = [_t(f"n{k}", f"N{k}", rnd.uniform(118, 140), rnd.uniform(2, 10)) for k in range(5)]
    start = time.perf_counter()
    report = insert_tracks(DJ, base, new)
    assert time.perf_counter() - start < 3
    assert _is_subsequence([t.id for t in base], report["song_ids_in_order"])


# ------------------------------------------------------------------ through MCP + fake Apple Music
ESTIMATES = [
    {"id": "4001", "artist": "Stephan Bodzin", "title": "Singularity", "bpm": 122, "energy": 3},
    {"id": "2003", "artist": "Amelie Lens", "title": "In My Mind", "bpm": 126, "energy": 5.5},
    {"id": "3001", "artist": "Adam Beyer & Bart Skils", "title": "Your Mind", "bpm": 129, "energy": 7},
    {"id": "2001", "artist": "Amelie Lens", "title": "Hypnotized", "bpm": 133, "energy": 9},
    {"id": "1001", "artist": "Charlotte de Witte", "title": "Doppler", "bpm": 134, "energy": 10},
    {"id": "8001", "artist": "Energy 52", "title": "Cafe del Mar", "bpm": 128, "energy": 5},
]


async def _setup(fake_apple, tmp_path):
    creds = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    server = build_server(lambda: build_app(creds))
    original = [e["id"] for e in ESTIMATES]
    return server, original


async def test_insert_tool_places_tracks_and_keeps_existing_order(fake_apple, tmp_path):
    server, original = await _setup(fake_apple, tmp_path)
    async with Client(server) as c:
        pid = (
            await c.call_tool("create_playlist", {"name": "Set", "song_ids": original})
        ).structured_content["playlist"]["id"]
        fake_apple.calls.clear()
        res = await c.call_tool(
            "insert_tracks_into_playlist",
            {
                "playlist_id": pid,
                "purpose": "dj_set",
                "current_estimates": ESTIMATES,
                "new_tracks": [
                    {"id": "4002", "artist": "Stephan Bodzin", "title": "Strand", "bpm": 121, "energy": 2.5},
                    {"id": "5001", "artist": "Boris Brejcha", "title": "Gravity", "bpm": 133, "energy": 9.5},
                ],
            },
        )
    data = res.structured_content
    final = fake_apple.playlists[pid]["tracks"]
    assert data["edit"]["verified_order"] and data["existing_order_preserved"]
    assert _is_subsequence(original, final)  # nothing that was there moved relative to each other
    assert final.index("4002") <= 1  # calm one opens the set
    assert final.index("2003") < final.index("5001") < final.index("8001")  # banger in the build/peak area
    placed = {p["id"]: p for p in data["inserted"]}
    assert placed["5001"]["act"] in {"Peak", "Melodic build"}
    # In place: same playlist id, and only the tail after the first insertion point was rewritten.
    assert data["edit"]["strategy"] == "in_place" and data["edit"]["playlist"]["id"] == pid


async def test_insert_tool_without_estimates_still_preserves_order(fake_apple, tmp_path):
    server, original = await _setup(fake_apple, tmp_path)
    async with Client(server) as c:
        pid = (
            await c.call_tool("create_playlist", {"name": "Set", "song_ids": original})
        ).structured_content["playlist"]["id"]
        res = await c.call_tool(
            "insert_tracks_into_playlist",
            {
                "playlist_id": pid,
                "purpose": "dj_set",
                "new_tracks": [{"id": "7001", "artist": "Eric Prydz", "bpm": 126, "energy": 7}],
            },
        )
    data = res.structured_content
    assert _is_subsequence(original, fake_apple.playlists[pid]["tracks"])
    assert any("no bpm/energy estimate" in w for w in data["warnings"])


async def test_insert_tool_asks_before_rebuilding_then_rebuilds_on_confirmation(fake_apple, tmp_path):
    fake_apple.remove_variants = set()  # account refuses in-place removal
    server, original = await _setup(fake_apple, tmp_path)
    args = {
        "purpose": "dj_set",
        "current_estimates": ESTIMATES,
        "new_tracks": [{"id": "4002", "artist": "Stephan Bodzin", "bpm": 121, "energy": 2.5}],
    }
    async with Client(server) as c:
        pid = (
            await c.call_tool("create_playlist", {"name": "Set", "song_ids": original})
        ).structured_content["playlist"]["id"]
        first = (
            await c.call_tool("insert_tracks_into_playlist", {"playlist_id": pid, **args})
        ).structured_content
        assert first["edit"]["status"] == "needs_decision"
        assert len(fake_apple.playlists) == 1  # not recreated without asking
        assert first["edit"]["decision"]["manual_steps"][0].startswith("Mover 'Stephan Bodzin - Strand'")
        # user says "pode recriar"
        res = await c.call_tool(
            "insert_tracks_into_playlist", {"playlist_id": pid, "allow_rebuild": True, **args}
        )
    edit = res.structured_content["edit"]
    assert edit["strategy"] == "rebuild" and edit["verified_order"]
    new_pid = edit["playlist"]["id"]
    assert pid not in fake_apple.playlists  # old one deleted after confirmation
    assert fake_apple.playlists[new_pid]["tracks"][0] == "4002"
    assert _is_subsequence(original, fake_apple.playlists[new_pid]["tracks"])


async def test_remove_keeps_the_order_of_the_rest(fake_apple, tmp_path):
    server, original = await _setup(fake_apple, tmp_path)
    async with Client(server) as c:
        pid = (
            await c.call_tool("create_playlist", {"name": "Set", "song_ids": original})
        ).structured_content["playlist"]["id"]
        res = await c.call_tool(
            "remove_tracks_from_playlist", {"playlist_id": pid, "song_ids": ["2003", "1001"]}
        )
    assert res.structured_content["verified_order"]
    assert fake_apple.playlists[pid]["tracks"] == [i for i in original if i not in {"2003", "1001"}]
