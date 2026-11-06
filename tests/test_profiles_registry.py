"""Profile families and profiles created in conversations (owner's decision, guide §7.3)."""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from apple_music_mcp.config import Credentials
from apple_music_mcp.container import AppContainer
from apple_music_mcp.curation.profiles import PROFILES, Act, Profile
from apple_music_mcp.server import build_server

from .conftest import build_app
from .fake_apple import FakeAppleMusic

LOFI = Profile(
    key="lofi_cooking",
    label="Lo-fi for cooking",
    description="Warm lo-fi with a light groove.",
    acts=(Act("Prep", 0.5, 3, 4, "warm"), Act("Cooking", 0.5, 4, 5, "light groove")),
    bpm_range=(70, 95),
    avg_track_minutes=3.0,
    vocals="sparse",
    aliases=("cozinhar", "cozinha", "cooking"),
    family="chill",
    origin="ai_created",
)


async def test_builtin_profiles_are_mirrored_and_custom_ones_persist(app: AppContainer) -> None:
    assert app.profiles.get("treino hiit").key == "hiit"
    app.profiles.create(LOFI)
    assert app.profiles.get("playlist para cozinhar").key == "lofi_cooking"
    reloaded = app.profiles.all()["lofi_cooking"]
    assert reloaded.family_key == "chill" and reloaded.origin == "ai_created"
    assert reloaded.acts == LOFI.acts and reloaded.bpm_range == (70, 95)
    assert app.profiles.delete("lofi_cooking") is True
    assert "lofi_cooking" not in app.profiles.all()


async def test_builtin_profiles_cannot_be_replaced_or_deleted(app: AppContainer) -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="built-in"):
        app.profiles.create(replace(LOFI, key="focus"))
    with pytest.raises(ValueError, match="unknown family"):
        app.profiles.create(replace(LOFI, family="nope"))
    assert app.profiles.delete("focus") is False


async def test_profile_tools_via_mcp(fake_apple: FakeAppleMusic) -> None:
    creds = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    async with Client(build_server(lambda: build_app(creds))) as c:
        listed = (await c.call_tool("list_playlist_profiles", {})).structured_content
        assert listed is not None
        families = {f["key"]: f for f in listed["families"]}
        assert {s["key"] for s in families["focus"]["sub_profiles"]} == {
            "study",
            "binaural",
            "epic_soundtrack",
        }

        args: dict[str, Any] = {
            "key": "Phonk Drive",
            "label": "Phonk for driving",
            "description": "Night drive phonk.",
            "family": "chill",
            "acts": [
                {"name": "Cruise", "share": 0.6, "energy_start": 5, "energy_end": 6, "intent": "smooth"},
                {"name": "Push", "share": 0.4, "energy_start": 7, "energy_end": 8, "intent": "aggressive"},
            ],
            "bpm_min": 120,
            "bpm_max": 150,
            "aliases": ["phonk dirigindo"],
        }
        created = (await c.call_tool("create_profile", args)).structured_content
        assert created is not None and created["created"] == "phonk_drive"
        curve = (
            await c.call_tool("profile_curve", {"purpose": "phonk dirigindo", "points": 3})
        ).structured_content
        assert curve is not None and curve["profile"] == "phonk_drive" and len(curve["curve"]) == 3

        plan = (
            await c.call_tool("plan_playlist", {"purpose": "phonk_drive", "duration_minutes": 60})
        ).structured_content
        assert plan is not None and plan["purpose"] == "phonk_drive"

        bad = await c.call_tool("create_profile", {**args, "key": "x", "bpm_min": 150, "bpm_max": 120})
        assert bad.is_error


def test_every_builtin_profile_round_trips_through_the_database_format() -> None:
    from apple_music_mcp.curation.registry import profile_from_row, profile_row

    for p in PROFILES.values():
        row = profile_row(p)
        row["family_slug"] = row["family"]
        assert profile_from_row(row) == p
