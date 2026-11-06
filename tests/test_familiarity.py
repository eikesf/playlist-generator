"""Familiarity mix: % known tracks and % known artists are separate targets."""

from __future__ import annotations

from pathlib import Path

from mcp import Client
from mcp.server.mcpserver import MCPServer

from apple_music_mcp.config import Credentials
from apple_music_mcp.container import AppContainer
from apple_music_mcp.curation.familiarity import MixTargets, mix_check, mix_counts
from apple_music_mcp.server import build_server

from .conftest import build_app
from .fake_apple import FakeAppleMusic


def test_mix_counts_splits_three_buckets() -> None:
    plan = mix_counts(20, MixTargets(known_tracks_pct=30, known_artists_pct=70))
    assert plan["counts"] == {"known_track": 6, "known_artist_new_track": 8, "new_artist": 6}
    assert sum(plan["counts"].values()) == 20
    assert plan["description"]["new_artist"] == "6 artistas novos"


def test_known_artists_cannot_be_below_known_tracks() -> None:
    plan = mix_counts(10, MixTargets(known_tracks_pct=50, known_artists_pct=30))
    assert plan["counts"] == {"known_track": 5, "known_artist_new_track": 0, "new_artist": 5}
    assert plan["warnings"]


def test_extremes() -> None:
    assert mix_counts(10, MixTargets(0, 0))["counts"] == {
        "known_track": 0,
        "known_artist_new_track": 0,
        "new_artist": 10,
    }
    assert mix_counts(10, MixTargets(100, 100))["counts"]["known_track"] == 10


def test_mix_check_suggests_swaps() -> None:
    targets = MixTargets(30, 70)
    check = mix_check({"known_track": 10, "known_artist_new_track": 6, "new_artist": 4}, targets)
    assert check["target"] == {"known_track": 6, "known_artist_new_track": 8, "new_artist": 6}
    assert not check["within_tolerance"]
    assert check["actual_known_tracks_pct"] == 50 and check["actual_known_artists_pct"] == 80
    assert any("replace" in s and "'known_track'" in s for s in check["suggestions"])
    ok = mix_check({"known_track": 6, "known_artist_new_track": 8, "new_artist": 6}, targets)
    assert ok["within_tolerance"] and ok["suggestions"] == []


def _server(fake_apple: FakeAppleMusic, tmp_path: Path) -> MCPServer:
    creds = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    return build_server(lambda: build_app(creds))


async def test_plan_playlist_includes_familiarity_mix(fake_apple: FakeAppleMusic, tmp_path: Path) -> None:
    async with Client(_server(fake_apple, tmp_path)) as c:
        plan = (
            await c.call_tool(
                "plan_playlist",
                {
                    "purpose": "set de techno",
                    "duration_minutes": 120,
                    "known_tracks_pct": 30,
                    "known_artists_pct": 70,
                },
            )
        ).structured_content
        plain = (
            await c.call_tool("plan_playlist", {"purpose": "treino", "duration_minutes": 60})
        ).structured_content
    assert plan["familiarity_mix"]["counts"] == {
        "known_track": 5,
        "known_artist_new_track": 8,
        "new_artist": 5,
    }
    assert "familiarity_mix" not in plain


async def test_resolve_with_targets_returns_mix_check(fake_apple: FakeAppleMusic, tmp_path: Path) -> None:
    tracks = ["Charlotte de Witte - Doppler", "Charlotte de Witte - Selected", "Amelie Lens - Feel It"]
    async with Client(_server(fake_apple, tmp_path)) as c:
        data = (
            await c.call_tool(
                "resolve_tracks", {"tracks": tracks, "known_tracks_pct": 20, "known_artists_pct": 60}
            )
        ).structured_content
    check = data["summary"]["mix_check"]
    assert check["actual"] == {"known_track": 2, "known_artist_new_track": 1, "new_artist": 0}
    assert check["target"] == {"known_track": 1, "known_artist_new_track": 1, "new_artist": 1}
    assert check["suggestions"] == ["replace 1 'known_track' track(s) with 'new_artist' track(s)"]


async def test_known_tracks_loved_first_and_deep_cuts_skip_known(
    app: AppContainer, fake_apple: FakeAppleMusic
) -> None:
    known = await app.discovery.known_tracks(["Charlotte de Witte", "Amelie Lens"])
    ids = [t["id"] for t in known["tracks"]]
    assert ids[0] == "2001" and known["tracks"][0]["loved"]  # loved first
    assert set(ids) == {"1001", "2001", "1002"}
    cuts = await app.discovery.deep_cuts(["Charlotte de Witte"])
    assert [t["id"] for t in cuts["tracks"]] == ["1003", "1004"]  # 1001 in library, 1002 recently played


async def test_collab_credit_counts_as_known_artist(app: AppContainer) -> None:
    profile = await app.taste.profile()
    assert profile.knows_artist("Eric Prydz & Someone")
    assert profile.bucket("9999", "Someone feat. Amelie Lens") == "known_artist_new_track"
    assert profile.bucket("9999", "Totally New Act") == "new_artist"
