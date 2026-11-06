"""One-time import of state kept in the ``setting`` table before Phase 3 (data model decision 6).

* ``refresh_plans``: refresh plans saved by the old ``plan``. Each becomes a ``refresh`` proposal,
  pending if it is younger than the proposal lifetime, expired otherwise (still kept: nothing lost).

The key is cleared after the import, so running this at every start imports nothing the second time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .db.history import ManagedPlaylistRepository, ProposalRepository, ProposedTrack
from .db.repositories import TrackInfo

if TYPE_CHECKING:
    from .container import AppContainer

REFRESH_PLANS_KEY = "refresh_plans"


def _track(candidate: dict[str, Any]) -> TrackInfo:
    sid = str(candidate.get("id") or "")
    artist, _, title = str(candidate.get("track") or sid).partition(" - ")
    return TrackInfo(
        title=title or artist,
        artist_name=artist if title else "",
        catalog_id=None if sid.startswith("i.") else sid or None,
        library_id=sid if sid.startswith("i.") else None,
    )


def _import_refresh_plans(app: AppContainer) -> int:
    plans = app.state.get(REFRESH_PLANS_KEY)
    if not plans:
        return 0
    proposals = ProposalRepository(app.db)
    managed = ManagedPlaylistRepository(app.db)
    now = datetime.now(UTC)
    imported = 0
    for playlist_id, plan in dict(plans).items():
        candidates = [(c, "not_played") for c in plan.get("rotation_candidates") or []]
        candidates += [(c, c.get("reason") or "disliked") for c in plan.get("removal_candidates") or []]
        if not candidates:
            continue
        created = datetime.fromtimestamp(float(plan.get("created_at") or now.timestamp()), UTC)
        fresh = (now - created).days < app.settings.proposal_ttl_days
        owner = managed.get(playlist_id)
        evidence = {
            str(c["id"]): {"why": why, **{k: c[k] for k in ("plays", "skips") if k in c}}
            for c, why in candidates
        }
        proposals.create(
            "refresh",
            [ProposedTrack(_track(c), action="remove") for c, _ in candidates],
            managed_playlist_id=owner["id"] if owner else None,
            status="pending_approval" if fresh else "expired",
            created_at=created,
            params={
                "playlist_id": playlist_id,
                "playlist_name": owner["name"] if owner else None,
                "imported_from": REFRESH_PLANS_KEY,
                "evidence": evidence,
                "notes": ["Imported from a refresh plan made before proposals existed."],
            },
        )
        imported += 1
    app.state.set(REFRESH_PLANS_KEY, None)
    return imported


def import_legacy_state(app: AppContainer) -> dict[str, int]:
    return {"refresh_plans": _import_refresh_plans(app), "pending_playlists": 0}
