"""Reviewing and approving scheduled proposals from a conversation (Desktop or Dispatch), via MCP."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp import Client

from apple_music_mcp.config import Credentials
from apple_music_mcp.container import AppContainer
from apple_music_mcp.db.history import ProposalRepository
from apple_music_mcp.jobs import run_job
from apple_music_mcp.local_music import MusicAppReader
from apple_music_mcp.server import build_server

from .conftest import build_app, result_text
from .fake_apple import FakeAppleMusic
from .test_refresh import MAIN, managed_main, week_of_listening
from .test_refresh_job import Recorder, opt_in


def factory(fake: FakeAppleMusic) -> Callable[[], AppContainer]:
    creds = Credentials(media_user_token=fake.user_token, storefront="br")

    def make() -> AppContainer:
        a = build_app(creds)
        a.music_app = MusicAppReader(system="Linux")
        return a

    return make


async def scheduled_proposal(fake: FakeAppleMusic) -> tuple[str, int]:
    """What the Monday job leaves behind: one pending refresh proposal for 'Warehouse'."""
    async with factory(fake)() as app:
        pl = await managed_main(app)
        week_of_listening(fake, app)
        opt_in(app, pl.id)
        await run_job("refresh_plan", app, Recorder())
        [p] = ProposalRepository(app.db).find(kind="refresh")
        return pl.id, int(p["id"])


async def call(c: Client, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    res = await c.call_tool(tool, args or {})
    assert not res.is_error, result_text(res)
    data: dict[str, Any] | None = res.structured_content
    assert data is not None
    return data


async def test_review_partial_approval_and_apply_through_mcp(fake_apple: FakeAppleMusic) -> None:
    pid, proposal_id = await scheduled_proposal(fake_apple)
    async with Client(build_server(factory(fake_apple))) as c:
        pending = await call(c, "list_pending_proposals")
        [summary] = pending["proposals"]
        assert summary["proposal_id"] == proposal_id and summary["playlist"]["name"] == "Warehouse"
        assert summary["summary"]["remove"] == 3

        shown = await call(c, "show_proposal", {"proposal_id": proposal_id})
        removes = {i["song_id"]: i for i in shown["items"] if i["action"] == "remove"}
        assert removes["2003"]["skips"] == 6 and removes["2003"]["why"] == "often_skipped"
        swap = removes["4002"]["replaced_by_item_id"]

        early = await c.call_tool("apply_proposal", {"proposal_id": proposal_id})
        assert early.is_error and "approved" in result_text(early)

        others = [
            i["item_id"] for i in shown["items"] if i["item_id"] not in {removes["4002"]["item_id"], swap}
        ]
        decided = await call(
            c,
            "decide_proposal",
            {
                "proposal_id": proposal_id,
                "accept_item_ids": [removes["4002"]["item_id"], swap],
                "reject": [{"item_id": i, "reason": "gosto dessa"} for i in others],
            },
        )
        assert decided["status"] == "approved" and decided["summary"]["rejected"] == len(others)
        applied = await call(c, "apply_proposal", {"proposal_id": proposal_id})
        after = await call(c, "list_pending_proposals")
    assert applied["status"] == "applied"
    main = fake_apple.playlists[pid]["tracks"]
    assert "4002" not in main and {"2003", "5002"} <= set(main)
    assert after["proposals"] == []


async def test_decide_in_two_steps_and_reject_everything(fake_apple: FakeAppleMusic) -> None:
    _, proposal_id = await scheduled_proposal(fake_apple)
    async with Client(build_server(factory(fake_apple))) as c:
        shown = await call(c, "show_proposal", {"proposal_id": proposal_id})
        first = shown["items"][0]["item_id"]
        partial = await call(
            c,
            "decide_proposal",
            {"proposal_id": proposal_id, "reject": [{"item_id": first, "reason": "fica"}]},
        )
        assert partial["status"] == "pending_approval"
        done = await call(
            c, "decide_proposal", {"proposal_id": proposal_id, "reject_all": True, "reason": "não"}
        )
        assert done["status"] == "rejected"
        reasons = {i["item_id"]: i["reason"] for i in done["items"]}
        assert reasons[first] == "fica" and set(reasons.values()) == {"fica", "não"}
        late = await c.call_tool("decide_proposal", {"proposal_id": proposal_id, "accept_all": True})
        both = await c.call_tool(
            "decide_proposal", {"proposal_id": proposal_id, "accept_all": True, "reject_all": True}
        )
        stranger = await c.call_tool(
            "decide_proposal", {"proposal_id": proposal_id, "accept_item_ids": [99999]}
        )
    assert late.is_error and both.is_error and stranger.is_error


async def test_expired_proposals_are_not_listed(fake_apple: FakeAppleMusic) -> None:
    async with factory(fake_apple)() as app:
        repo = ProposalRepository(app.db)
        old = repo.create(
            "refresh", [], params={"playlist_id": "p.x"}, created_at=datetime.now(UTC) - timedelta(days=8)
        )
    async with Client(build_server(factory(fake_apple))) as c:
        listed = await call(c, "list_pending_proposals")
        shown = await call(c, "show_proposal", {"proposal_id": old})
    assert listed["proposals"] == [] and shown["status"] == "expired"


async def test_apply_with_estimates_places_the_replacement(fake_apple: FakeAppleMusic) -> None:
    pid, proposal_id = await scheduled_proposal(fake_apple)
    async with Client(build_server(factory(fake_apple))) as c:
        shown = await call(c, "show_proposal", {"proposal_id": proposal_id})
        await call(c, "decide_proposal", {"proposal_id": proposal_id, "accept_all": True})
        adds = [i for i in shown["items"] if i["action"] == "add"]
        estimates = [
            {"id": i["song_id"], "artist": i["line"].split(" - ")[0], "bpm": 124, "energy": 6} for i in adds
        ] + [{"id": "4001", "artist": "Stephan Bodzin", "bpm": 122, "energy": 3}]
        applied = await call(c, "apply_proposal", {"proposal_id": proposal_id, "estimates": estimates})
    main = fake_apple.playlists[pid]["tracks"]
    assert applied["status"] == "applied" and {i["song_id"] for i in adds} <= set(main)
    assert not {"2003", "4002", "5002"} & set(main)
    assert [t for t in main if t in MAIN] == [t for t in MAIN if t not in {"2003", "4002", "5002"}]
