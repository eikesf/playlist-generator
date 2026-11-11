"""Expired login never forces an export: the playlist is kept and created after `login`."""

from __future__ import annotations

import pytest
from mcp import Client

from apple_music_mcp.commands.pending import run_finish_pending
from apple_music_mcp.config import Credentials, save_credentials
from apple_music_mcp.server import build_server

from .conftest import build_app, result_text
from .fake_apple import FakeAppleMusic


async def test_expired_login_keeps_playlist_pending_then_finishes(fake_apple: FakeAppleMusic) -> None:
    expired = Credentials(media_user_token="expired-token-xxxxxxxxxxxx", storefront="br")
    async with Client(build_server(lambda: build_app(expired))) as c:
        res = await c.call_tool(
            "create_playlist", {"name": "Warehouse", "description": "set", "song_ids": ["4001", "1001"]}
        )
        tools = {t.name for t in (await c.list_tools()).tools}
    assert res.is_error and "AUTH" in result_text(res) and "pending" in result_text(res)
    assert "export_playlist_text" not in tools
    assert fake_apple.playlists == {}

    fresh = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    async with Client(build_server(lambda: build_app(fresh))) as c:
        done = (await c.call_tool("finish_pending_playlists", {})).structured_content
        again = (await c.call_tool("finish_pending_playlists", {})).structured_content
    [created] = done["created"]
    assert created["playlist"]["name"] == "Warehouse"
    assert fake_apple.playlists[created["playlist"]["id"]]["tracks"] == ["4001", "1001"]
    assert again["note"] == "No pending playlists."


async def test_discard_pending(fake_apple: FakeAppleMusic) -> None:
    expired = Credentials(media_user_token="expired-token-xxxxxxxxxxxx")
    async with Client(build_server(lambda: build_app(expired))) as c:
        await c.call_tool("create_playlist", {"name": "Old idea", "song_ids": ["4001"]})
    fresh = Credentials(media_user_token=fake_apple.user_token)
    async with Client(build_server(lambda: build_app(fresh))) as c:
        res = (await c.call_tool("finish_pending_playlists", {"discard": ["Old idea"]})).structured_content
    assert res["discarded"] == ["Old idea"] and res["created"] == []
    assert fake_apple.playlists == {}


async def test_finish_pending_cli(fake_apple: FakeAppleMusic) -> None:
    expired = Credentials(media_user_token="expired-token-xxxxxxxxxxxx")
    async with Client(build_server(lambda: build_app(expired))) as c:
        await c.call_tool("create_playlist", {"name": "Run", "song_ids": ["2001"]})
    save_credentials(Credentials(media_user_token=fake_apple.user_token, storefront="br"))
    lines: list[str] = []
    assert await run_finish_pending(build=build_app, out=lines.append) == 0
    assert lines[0].startswith("Created 'Run' with 1 tracks")
    lines.clear()
    assert await run_finish_pending(build=build_app, out=lines.append) == 0
    assert lines == ["No pending playlists."]


async def test_pending_playlists_are_approved_create_proposals(fake_apple: FakeAppleMusic) -> None:
    from apple_music_mcp.db.history import ProposalRepository
    from apple_music_mcp.operations import PendingPlaylists

    expired = Credentials(media_user_token="expired-token-xxxxxxxxxxxx", storefront="br")
    async with Client(build_server(lambda: build_app(expired))) as c:
        await c.call_tool("create_playlist", {"name": "Run", "song_ids": ["2001", "i.lib1"]})
        await c.call_tool("create_playlist", {"name": "Run", "song_ids": ["2002"]})  # same name: replaces
    fresh = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    async with build_app(fresh) as app:
        repo = ProposalRepository(app.db)
        [p] = repo.find(statuses=("approved",), kind="create")
        assert p["params"]["pending"] is True and p["params"]["song_ids"] == ["2002"]
        assert len(repo.find(statuses=("rejected",), kind="create")) == 1  # the replaced draft is kept
        [draft] = PendingPlaylists(app).list()
        assert draft["name"] == "Run" and draft["proposal_id"] == p["id"]
        [done] = await PendingPlaylists(app).finish()
        assert repo.get(p["id"])["status"] == "applied"  # type: ignore[index]
        assert fake_apple.playlists[done["playlist"]["id"]]["tracks"] == ["2002"]
        assert PendingPlaylists(app).list() == []


async def test_finish_with_login_still_expired_keeps_one_pending(fake_apple: FakeAppleMusic) -> None:
    from apple_music_mcp.apple.errors import AuthError
    from apple_music_mcp.operations import PendingPlaylists

    expired = Credentials(media_user_token="expired-token-xxxxxxxxxxxx", storefront="br")
    async with build_app(expired) as app:
        await PendingPlaylists(app).add("Focus", None, ["4001"])
        with pytest.raises(AuthError):
            await PendingPlaylists(app).finish()
        assert [d["name"] for d in PendingPlaylists(app).list()] == ["Focus"]


async def test_legacy_pending_key_is_imported_once(fake_apple: FakeAppleMusic) -> None:
    from apple_music_mcp.legacy import PENDING_KEY, import_legacy_state
    from apple_music_mcp.operations import PendingPlaylists

    async with build_app(Credentials()) as app:
        app.state.set(
            PENDING_KEY, [{"name": "Old", "description": "d", "song_ids": ["1001", "2001"], "saved_at": 1.0}]
        )
        assert import_legacy_state(app)["pending_playlists"] == 1
        assert import_legacy_state(app)["pending_playlists"] == 0
        [draft] = PendingPlaylists(app).list()
        assert (draft["name"], draft["description"], draft["song_ids"]) == ("Old", "d", ["1001", "2001"])
        assert app.state.get(PENDING_KEY) is None


def test_a_track_known_only_by_id_never_overwrites_real_names() -> None:
    from sqlalchemy import select

    from apple_music_mcp.db import Database
    from apple_music_mcp.db.repositories import TrackInfo, TrackRepository
    from apple_music_mcp.db.schema import track

    db = Database.open()
    repo = TrackRepository(db)
    real = repo.upsert(TrackInfo("Doppler", "Charlotte de Witte", catalog_id="1001"))
    assert repo.upsert(TrackInfo("1001", "", catalog_id="1001")) == real
    with db.engine.connect() as conn:
        row = conn.execute(select(track.c.title, track.c.artist_name).where(track.c.id == real)).one()
    assert tuple(row) == ("Doppler", "Charlotte de Witte")
    db.dispose()
