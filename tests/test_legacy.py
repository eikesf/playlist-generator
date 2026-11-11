"""One-time import of state kept in the `setting` table before Phase 3 (nothing lost, never twice)."""

from __future__ import annotations

import time

from apple_music_mcp.config import Credentials
from apple_music_mcp.db.history import ProposalRepository
from apple_music_mcp.legacy import REFRESH_PLANS_KEY, import_legacy_state

from .conftest import build_app


def test_old_refresh_plans_become_proposals_once() -> None:
    with_plans = {
        "p.recent": {
            "rotation_candidates": [
                {"id": "4002", "track": "Stephan Bodzin - Strand", "plays": 1, "skips": 0}
            ],
            "removal_candidates": [{"id": "5002", "track": "Boris Brejcha - Gravity (Ann Clue Remix)"}],
            "created_at": time.time() - 2 * 86400,
        },
        "p.old": {
            "rotation_candidates": [
                {"id": "i.abc", "track": "Someone - Library Only", "plays": 0, "skips": 3}
            ],
            "removal_candidates": [],
            "created_at": time.time() - 30 * 86400,
        },
    }
    app = build_app(Credentials())
    app.state.set(REFRESH_PLANS_KEY, with_plans)
    assert import_legacy_state(app) == {"refresh_plans": 2, "pending_playlists": 0}
    repo = ProposalRepository(app.db)
    rows = {p["params"]["playlist_id"]: p for p in repo.find(statuses=("pending_approval", "expired"))}
    assert rows["p.recent"]["status"] == "pending_approval" and rows["p.old"]["status"] == "expired"
    recent = repo.get(rows["p.recent"]["id"])
    assert recent is not None
    got = {(i["catalog_id"], i["action"]) for i in recent["items"]}
    assert got == {("4002", "remove"), ("5002", "remove")}
    assert {i["artist_name"] for i in recent["items"]} == {"Stephan Bodzin", "Boris Brejcha"}
    old = repo.get(rows["p.old"]["id"])
    assert old is not None and old["items"][0]["library_id"] == "i.abc"
    assert rows["p.recent"]["params"]["imported_from"] == "refresh_plans"
    assert app.state.get(REFRESH_PLANS_KEY) is None
    # Running again (every container start) imports nothing.
    assert import_legacy_state(app) == {"refresh_plans": 0, "pending_playlists": 0}
    assert len(repo.find(statuses=("pending_approval", "expired"))) == 2
    app.db.dispose()


def test_container_start_imports_legacy_state() -> None:
    first = build_app(Credentials())
    first.state.set(REFRESH_PLANS_KEY, {"p1": {"rotation_candidates": [], "removal_candidates": []}})
    first.db.dispose()
    second = build_app(Credentials())
    assert second.state.get(REFRESH_PLANS_KEY) is None
    second.db.dispose()
