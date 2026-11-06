"""Proposals waiting for the owner (data model decision 6): show, decide per item, apply, expire.

Every scheduled change (refresh, promotion) is a proposal first. The owner decides in a Claude
conversation (Desktop or Dispatch), item by item or all at once, and only accepted items are ever
applied. Lifecycle: ``pending_approval`` → ``approved`` (at least one item accepted, none pending) or
``rejected`` (everything rejected) → ``applied``; undecided proposals become ``expired``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from .container import AppContainer
from .db.history import ProposalRepository
from .db.repositories import utcnow
from .models import format_duration

DECIDABLE_KINDS = ("refresh", "promotion")


def _song_id(item: dict[str, Any]) -> str:
    return str(item.get("catalog_id") or item.get("library_id"))


class ProposalService:
    def __init__(self, app: AppContainer):
        self.app = app
        self.repo = ProposalRepository(app.db)

    # ------------------------------------------------------------------ reading
    def render(self, proposal_id: int) -> dict[str, Any]:
        p = self.repo.get(proposal_id)
        if p is None:
            raise ValueError(f"unknown proposal {proposal_id}")
        params = p["params"] or {}
        evidence = params.get("evidence") or {}
        items = []
        replaced_by = {i["replaces_item_id"]: i["id"] for i in p["items"] if i["replaces_item_id"]}
        for i in p["items"]:
            sid = _song_id(i)
            items.append(
                {
                    "item_id": i["id"],
                    "action": i["action"],
                    "song_id": sid,
                    "line": f"{i['artist_name']} - {i['title']}",
                    "duration": format_duration(i["duration_ms"]),
                    "familiarity": i["bucket"],
                    "decision": i["decision"],
                    "reason": i["reason"],
                    "replaces_item_id": i["replaces_item_id"],
                    "replaced_by_item_id": replaced_by.get(i["id"]),
                    **(evidence.get(sid) or {}),
                }
            )
        counts = {"remove": 0, "add": 0, "pending": 0, "accepted": 0, "rejected": 0}
        for i in items:
            if i["action"] in counts:
                counts[i["action"]] += 1
            counts[i["decision"]] += 1
        return {
            "proposal_id": p["id"],
            "kind": p["kind"],
            "status": p["status"],
            "playlist": {"id": params.get("playlist_id"), "name": params.get("playlist_name")},
            "created_at": p["created_at"],
            "notified_at": p["notified_at"],
            "summary": counts,
            "window": params.get("window"),
            "notes": params.get("notes") or [],
            **({"grow_by": params["grow_by"]} if "grow_by" in params else {}),
            "items": items,
        }

    def summary(self, p: dict[str, Any]) -> dict[str, Any]:
        full = self.render(p["id"])
        return {k: full[k] for k in ("proposal_id", "kind", "status", "playlist", "created_at", "summary")}

    def expire_stale(self, now: datetime | None = None) -> list[int]:
        ttl = timedelta(days=self.app.settings.proposal_ttl_days)
        return self.repo.expire_pending(DECIDABLE_KINDS, (now or utcnow()) - ttl)

    def pending(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Proposals waiting for the owner (expired ones are retired first)."""
        self.expire_stale()
        rows = self.repo.find(statuses=("pending_approval", "approved"), kind=kind)
        return [self.summary(p) for p in rows if p["kind"] in DECIDABLE_KINDS]

    # ----------------------------------------------------------------- deciding
    def decide(
        self,
        proposal_id: int,
        *,
        accept: Sequence[int] = (),
        reject: Sequence[tuple[int, str | None]] = (),
        accept_all: bool = False,
        reject_all: bool = False,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Accept/reject items (by item id). Once nothing is pending the proposal is approved
        (something accepted) or rejected (nothing accepted)."""
        if accept_all and reject_all:
            raise ValueError("choose accept_all or reject_all, not both")
        p = self.repo.get(proposal_id)
        if p is None:
            raise ValueError(f"unknown proposal {proposal_id}")
        if p["kind"] not in DECIDABLE_KINDS:
            raise ValueError(f"{p['kind']} proposals are not decided here")
        if p["status"] != "pending_approval":
            raise ValueError(
                f"proposal {proposal_id} is {p['status']}; only a pending proposal can be decided"
            )
        item_ids = {i["id"] for i in p["items"]}
        unknown = sorted(({*accept} | {i for i, _ in reject}) - item_ids)
        if unknown:
            raise ValueError(f"items {unknown} do not belong to proposal {proposal_id}")
        decisions: dict[int, tuple[str, str | None]] = {}
        if accept_all or reject_all:
            for i in p["items"]:
                if i["decision"] == "pending":
                    decisions[i["id"]] = ("accepted", None) if accept_all else ("rejected", reason)
        decisions.update({i: ("accepted", None) for i in accept})
        decisions.update({i: ("rejected", r or reason) for i, r in reject})
        self.repo.decide_by_item(proposal_id, decisions)
        after = self.repo.get(proposal_id)
        assert after is not None
        if all(i["decision"] != "pending" for i in after["items"]):
            accepted = any(i["decision"] == "accepted" for i in after["items"])
            self.repo.set_status(proposal_id, "approved" if accepted else "rejected")
        return self.render(proposal_id)

    # ----------------------------------------------------------------- applying
    async def apply(
        self,
        proposal_id: int,
        *,
        purpose: str | None = None,
        estimates: list[dict[str, Any]] | None = None,
        allow_rebuild: bool = False,
    ) -> dict[str, Any]:
        p = self.repo.get(proposal_id)
        if p is None:
            raise ValueError(f"unknown proposal {proposal_id}")
        if p["status"] != "approved":
            raise ValueError(f"proposal {proposal_id} is {p['status']}; only an approved proposal is applied")
        if p["kind"] == "refresh":
            from .refresh import RefreshService

            return await RefreshService(self.app, self.app.music_app).apply_refresh(
                proposal_id, purpose=purpose, estimates=estimates, allow_rebuild=allow_rebuild
            )
        if p["kind"] == "promotion":
            from .promotion import PromotionService

            return await PromotionService(self.app).apply(
                proposal_id, purpose=purpose, estimates=estimates, allow_rebuild=allow_rebuild
            )
        raise ValueError(f"{p['kind']} proposals are not applied here")
