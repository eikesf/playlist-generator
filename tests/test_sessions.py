"""Guide §7.1-7.7, §8.1: edit-or-create sessions, preview before creating, managed playlists, history."""

from __future__ import annotations

from typing import Any

from mcp import Client

from apple_music_mcp.config import Credentials
from apple_music_mcp.container import AppContainer
from apple_music_mcp.server import build_server
from apple_music_mcp.sessions import CurationSessions, PreviewTrack, sync_with_history

from .conftest import build_app
from .fake_apple import FakeAppleMusic

IDS = ["4001", "4002", "2003", "1001"]


async def test_preview_creates_nothing_until_approved(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    s = CurationSessions(app)
    started = s.start("create", "um set de techno de 2h")
    sid = started["session_id"]
    assert s.record_answer(sid, "Duração?", "2 horas") == 1
    before = len(fake_apple.playlists)
    preview = await s.preview(
        sid,
        "Warehouse Ascension",
        [PreviewTrack(i, section="Opening" if n < 2 else "Peak") for n, i in enumerate(IDS)],
        purpose="melodic techno",
        params={"duration_minutes": 120, "known_artists_pct": 50, "known_tracks_pct": 15},
    )
    assert len(fake_apple.playlists) == before  # a preview never touches Apple Music
    assert [sec["section"] for sec in preview["sections"]] == ["Opening", "Peak"]
    assert preview["track_count"] == 4 and preview["purpose"] == "melodic_techno"

    after_reject = s.reject(preview["proposal_id"], [("2003", "muito pesada")])
    assert after_reject["track_count"] == 3
    assert after_reject["rejected"][0]["reason"] == "muito pesada"

    created = await s.approve(preview["proposal_id"])
    pl_id = created["playlist"]["id"]
    assert fake_apple.playlists[pl_id]["tracks"] == ["4001", "4002", "1001"]  # order kept, rejected left out

    managed = s.managed.get(pl_id)
    assert managed is not None and managed["profile"] == "melodic_techno" and managed["origin"] == "created"
    assert managed["known_artists_pct"] == 50 and managed["target_duration_min"] == 120
    history = s.events.history(managed["id"])
    assert {e["event"] for e in history} == {"added"} and len(history) == 3

    # The rejection is remembered for the next session.
    nxt = s.start("create", "outro set")
    assert any(r["catalog_id"] == "2003" and r["reason"] == "muito pesada" for r in nxt["avoid"])

    # A pending preview can be approved once only.
    try:
        await s.approve(preview["proposal_id"])
    except ValueError as exc:
        assert "applied" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("approving twice must fail")


async def test_edit_session_loads_the_managed_settings(app: AppContainer) -> None:
    s = CurationSessions(app)
    pl = await app.library.create_playlist("Gym")
    s.adopt(pl.id, "Gym", "treino hiit", known_artists_pct=60, known_tracks_pct=20)
    started = s.start("edit", "adicionar 5 músicas", playlist_id=pl.id)
    assert started["managed_playlist"]["profile"] == "hiit"
    assert "redefines the whole profile" in started["note"]


async def test_edits_on_managed_playlists_are_recorded(app: AppContainer, fake_apple: FakeAppleMusic) -> None:
    s = CurationSessions(app)
    pl = await app.library.create_playlist("Set")
    await app.library.add_tracks(pl.id, ["4001", "4002"])
    s.adopt(pl.id, "Set", "dj set")
    result = await sync_with_history(app, pl.id, ["4001", "1001"])
    assert result.status == "applied"
    managed = s.managed.get(result.playlist.id)
    assert managed is not None
    events = {(e["event"], e["catalog_id"]) for e in s.events.history(managed["id"])}
    assert events == {("added", "1001"), ("removed", "4002")}


async def test_unmanaged_playlists_get_no_history(app: AppContainer) -> None:
    pl = await app.library.create_playlist("Plain")
    await app.library.add_tracks(pl.id, ["4001"])
    await sync_with_history(app, pl.id, ["4001", "4002"])
    assert CurationSessions(app).managed.get(pl.id) is None


async def test_session_flow_via_mcp(fake_apple: FakeAppleMusic) -> None:
    creds = Credentials(media_user_token=fake_apple.user_token, storefront="br")
    async with Client(build_server(lambda: build_app(creds))) as c:

        async def call(name: str, args: dict[str, Any]) -> dict[str, Any]:
            r = await c.call_tool(name, args)
            assert not r.is_error, r.content
            data = r.structured_content
            assert isinstance(data, dict)
            return data

        sid = (await call("start_session", {"intent": "create", "request": "foco binaural 1h"}))["session_id"]
        await call("record_answer", {"session_id": sid, "question": "Duração?", "answer": "1h"})
        preview = await call(
            "preview_playlist",
            {
                "session_id": sid,
                "name": "Binaural Focus",
                "purpose": "binaural",
                "tracks": [{"song_id": i, "section": "Hold"} for i in IDS[:2]],
                "known_artists_pct": 40,
                "known_tracks_within_known_artists_pct": 25,
            },
        )
        created = await call("approve_preview", {"proposal_id": preview["proposal_id"]})
        pl_id = created["playlist"]["id"]
        managed = await call("list_managed_playlists", {})
        assert managed["count"] == 1 and managed["playlists"][0]["known_tracks_pct"] == 10
        updated = await call("update_managed_playlist", {"playlist_id": pl_id, "refresh_enabled": True})
        assert updated["managed_playlist"]["refresh_enabled"] is True
        history = await call("playlist_history", {"playlist_id": pl_id})
        assert history["managed"] and len(history["events"]) == 2
