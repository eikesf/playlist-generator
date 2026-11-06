"""Full simulated session: what Claude does when the user asks for a techno set."""

from __future__ import annotations

from pathlib import Path

from mcp import Client

from apple_music_mcp.config import Credentials, load_credentials
from apple_music_mcp.server import build_server

from .conftest import build_app
from .fake_apple import FakeAppleMusic


async def test_techno_set_is_created_in_order(fake_apple: FakeAppleMusic, tmp_path: Path) -> None:
    creds = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    server = build_server(lambda: build_app(creds))

    # Journey order chosen by the curator: atmospheric → groove → build → peak → cooldown.
    journey = [
        "Stephan Bodzin - Singularity",
        "Stephan Bodzin - Strand",
        "Amelie Lens - In My Mind",
        "Adam Beyer, Bart Skils - Your Mind",
        "Charlotte de Witte - Doppler",
        "Age of Love - Age of Love (Charlotte de Witte & Enrico Sangiuliano Remix)",
        "Amelie Lens - Hypnotized",
        "Charlotte de Witte - Doppler (Radio Edit)",  # dup: must be dropped
        "Boris Brejcha - Gravity (Ann Clue Remix)",
        "Imaginary Artist - Not In Catalog",  # must be reported, never guessed
        "Energy 52 - Cafe del Mar (Michael Woods Remix)",
    ]

    async with Client(server) as c:
        status = (await c.call_tool("auth_status", {})).structured_content
        assert status["ready_to_create_playlists"]

        sample = (
            await c.call_tool("artist_top_songs", {"artist": "Charlotte de Witte", "limit": 3})
        ).structured_content
        assert len(sample["songs"]) == 3

        report = (await c.call_tool("resolve_tracks", {"tracks": journey})).structured_content
        ids = report["song_ids_in_order"]
        assert ids == ["4001", "4002", "2003", "3001", "1001", "6001", "2001", "5002", "8001"]
        assert report["summary"]["duplicates_removed"] == 1
        assert report["summary"]["total_duration"].startswith("0:") is False

        created = (
            await c.call_tool(
                "create_playlist",
                {"name": "Warehouse Ascension", "description": "A techno set that builds.", "song_ids": ids},
            )
        ).structured_content
        assert created["created"] and created["tracks_added"] == len(ids)
        pid = created["playlist"]["id"]

        tracks = (await c.call_tool("get_playlist_tracks", {"playlist_id": pid})).structured_content
        assert [t["catalog_id"] for t in tracks["tracks"]] == ids

        listing = (await c.call_tool("list_playlists", {})).structured_content
        assert [p["name"] for p in listing["playlists"]] == ["Warehouse Ascension"]

    assert fake_apple.playlists[pid]["tracks"] == ids
    # The scraped developer token was cached for the next session.
    assert load_credentials().developer_token == fake_apple.dev_token


async def test_create_playlist_resolves_free_text_in_one_call(
    fake_apple: FakeAppleMusic, tmp_path: Path
) -> None:
    creds = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    server = build_server(lambda: build_app(creds))
    async with Client(server) as c:
        created = (
            await c.call_tool(
                "create_playlist",
                {
                    "name": "Quick",
                    "tracks": ["Eric Prydz - Opus", "Amelie Lens - Feel It", "Nobody - Zzz Qqq"],
                },
            )
        ).structured_content
    assert created["tracks_added"] == 2
    assert [t["request"] for t in created["not_added"]] == ["Nobody - Zzz Qqq"]
    pid = created["playlist"]["id"]
    assert fake_apple.playlists[pid]["tracks"] == ["7001", "2002"]


async def test_purpose_driven_flow_plan_resolve_order_create(
    fake_apple: FakeAppleMusic, tmp_path: Path
) -> None:
    """User: "a short techno set that builds like a DJ set". Claude follows the skill's steps."""
    creds = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    server = build_server(lambda: build_app(creds))
    # Claude's estimates (energy 1-10, BPM) for tracks it proposes, in arbitrary order.
    estimates = {
        "Boris Brejcha - Gravity (Ann Clue Remix)": (130, 8),
        "Stephan Bodzin - Singularity": (122, 3),
        "Charlotte de Witte - Doppler": (134, 9.5),
        "Amelie Lens - Feel It": (128, 6.5),
        "Stephan Bodzin - Strand": (121, 4),
        "Amelie Lens - Hypnotized": (133, 9),
        "Energy 52 - Cafe del Mar (Michael Woods Remix)": (128, 6),
        "Adam Beyer, Bart Skils - Your Mind": (130, 7.5),
    }
    async with Client(server) as c:
        plan = (
            await c.call_tool("plan_playlist", {"purpose": "set de techno", "track_count": 8})
        ).structured_content
        assert plan["purpose"] == "dj_set" and plan["total_tracks"] == 8

        report = (await c.call_tool("resolve_tracks", {"tracks": list(estimates)})).structured_content
        assert report["summary"]["matched"] == 8
        features = []
        for row in report["tracks"]:
            bpm, energy = estimates[row["request"]]
            song = row["song"]
            features.append(
                {
                    "id": song["id"],
                    "artist": song["artist"],
                    "title": song["name"],
                    "bpm": bpm,
                    "energy": energy,
                }
            )

        ordered = (
            await c.call_tool("order_tracks", {"purpose": "dj_set", "tracks": features})
        ).structured_content
        ids = ordered["song_ids_in_order"]
        assert ids[0] == "4001"  # Singularity (energy 3) opens
        energies = [t["energy"] for t in ordered["tracks"]]
        assert energies.index(max(energies)) >= 4  # peak in the second half

        created = (
            await c.call_tool("create_playlist", {"name": "Hypnotic Ascent", "song_ids": ids})
        ).structured_content
        pid = created["playlist"]["id"]

    assert fake_apple.playlists[pid]["tracks"] == ids
