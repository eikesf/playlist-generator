"""Proposal tools: review and approve what the scheduled jobs prepared (guide §7.10, §7.11).

Works the same on the Mac and through Dispatch on the phone: list what is waiting, show one with its
evidence, decide item by item (or all at once) with the owner's reasons, then apply. Only accepted
items are ever applied.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ..proposals import ProposalService
from .base import ContainerProvider, tool_errors
from .curation_tools import TrackInput


class ItemRejection(BaseModel):
    item_id: int
    reason: str | None = Field(default=None, description="Why, in the user's words")


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    def service() -> ProposalService:
        return ProposalService(provider.get())

    @mcp.tool()
    @tool_errors
    async def list_pending_proposals(kind: Literal["refresh", "promotion"] | None = None) -> dict[str, Any]:
        """Proposals waiting for the user: weekly refreshes and promotions from Weekly Discoveries
        (expired ones are retired first). Each has counts of tracks leaving and entering. Use it when
        the user says he got a notification ("refresh pronto") or asks what is pending."""
        rows = service().pending(kind)
        return {"count": len(rows), "proposals": rows}

    @mcp.tool()
    @tool_errors
    async def show_proposal(proposal_id: int) -> dict[str, Any]:
        """One proposal with every item: `action` (remove/add), the track, `why` (often_skipped,
        not_played, disliked, replacement, liked), plays/skips in the window, familiarity, and the
        link between a removal and its replacement (`replaced_by_item_id` / `replaces_item_id`).
        Show it by name, each removal next to its replacement, and read the `notes` aloud."""
        return service().render(proposal_id)

    @mcp.tool()
    @tool_errors
    async def decide_proposal(
        proposal_id: int,
        accept_item_ids: list[int] | None = None,
        reject: list[ItemRejection] | None = None,
        accept_all: bool = False,
        reject_all: bool = False,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Record the user's decision per item (ids from show_proposal). Ask before deciding: never
        accept a removal he did not confirm. Items can be decided over several calls. `accept_all` /
        `reject_all` cover the items still pending (`reason` applies to the rejected ones). When
        nothing is pending, the proposal becomes `approved` (then call apply_proposal) or `rejected`."""
        return service().decide(
            proposal_id,
            accept=accept_item_ids or [],
            reject=[(r.item_id, r.reason) for r in reject or []],
            accept_all=accept_all,
            reject_all=reject_all,
            reason=reason,
        )

    @mcp.tool()
    @tool_errors
    async def apply_proposal(
        proposal_id: int,
        estimates: list[TrackInput] | None = None,
        purpose: str | None = None,
        allow_rebuild: bool = False,
    ) -> dict[str, Any]:
        """Apply an approved proposal: ONLY the accepted items. Removed tracks are first copied to
        "<name> · Arquivo" and verified (nothing is ever lost); new tracks are inserted at the position
        that fits the vibe, keeping the existing order. `estimates`: bpm/energy for the new tracks and,
        ideally, the current ones (ids from get_playlist_tracks) for better placement. On status
        `needs_decision`, ask the user (recreate vs manual) before calling again with allow_rebuild."""
        return await service().apply(
            proposal_id,
            purpose=purpose,
            estimates=[t.model_dump() for t in estimates or []],
            allow_rebuild=allow_rebuild,
        )
