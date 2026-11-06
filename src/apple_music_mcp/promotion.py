"""Promotion from Weekly Discoveries into the main playlist (guide §7.11), always approved by the owner.

* **Liked** [PADRÃO, configurable]: loved (Apple rating or Music app favorite), or played at least
  ``PROMOTE_MIN_PLAYS`` (3) times with a skip ratio below ``PROMOTE_MAX_SKIP_RATIO`` (0.25) since the
  batch that brought it, measured from the daily snapshots.
* **Cap**: at most ``PROMOTE_WEEKLY_FRACTION`` (20 %) of the main playlist per week, rounded down,
  never above ``PROMOTE_WEEKLY_MAX`` (30), counting promotions already applied in the last 7 days.
* A ``promotion`` proposal is built by the weekly job (read-only); after approval the accepted
  tracks are inserted at their best position (``Sequencer.insert``, existing order kept), recorded
  with origin ``promotion``, and leave the weekly playlist.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .apple.errors import AppleMusicError
from .container import AppContainer
from .curation.listening import Delta, ListeningWindow
from .db.history import ManagedPlaylistRepository, ProposalRepository, ProposedTrack
from .db.repositories import ListeningRepository, TrackInfo, match_key
from .editing import TrackRef


def promotion_cap(main_size: int, already_this_week: int, fraction: float = 0.2, maximum: int = 30) -> int:
    return max(0, min(maximum, math.floor(main_size * fraction)) - already_this_week)


def is_liked(delta: Delta | None, loved: bool, min_plays: int, max_skip_ratio: float) -> str | None:
    if loved or (delta is not None and delta.favorited):
        return "loved"
    if delta is not None and delta.plays >= min_plays and (delta.skip_ratio or 0) < max_skip_ratio:
        return f"played {delta.plays}x, skipped {delta.skips}x"
    return None


def _as_date(value: Any) -> date:
    return datetime.fromisoformat(str(value)).date()


class PromotionService:
    def __init__(self, app: AppContainer):
        self.app = app
        self.proposals = ProposalRepository(app.db)
        self.managed = ManagedPlaylistRepository(app.db)

    def _batch_dates(self, managed_id: int) -> dict[str, date]:
        """Catalog id -> the day its latest weekly batch was submitted."""
        out: dict[str, date] = {}
        for p in reversed(
            self.proposals.find(statuses=("applied",), kind="discoveries", managed_playlist_id=managed_id)
        ):
            full = self.proposals.get(p["id"])
            for item in (full or {}).get("items", []):
                if item["catalog_id"]:
                    out[str(item["catalog_id"])] = _as_date(p["created_at"])
        return out

    def promoted_this_week(self, managed_id: int, now: datetime | None = None) -> int:
        since = (now or datetime.now(UTC)) - timedelta(days=7)
        count = 0
        for p in self.proposals.find(statuses=("applied",), kind="promotion", managed_playlist_id=managed_id):
            if p["applied_at"] and datetime.fromisoformat(p["applied_at"]).replace(tzinfo=UTC) >= since:
                full = self.proposals.get(p["id"])
                count += sum(1 for i in (full or {}).get("items", []) if i["decision"] == "accepted")
        return count

    async def build(
        self, managed: dict[str, Any], *, job_run_id: int | None = None, today: date | None = None
    ) -> dict[str, Any]:
        """A promotion proposal for one managed playlist, or ``proposal_id: None`` when nothing qualifies."""
        from .proposals import ProposalService
        from .weekly_discoveries import WeeklyDiscoveries

        open_one = self.proposals.open_for("promotion", managed_playlist_id=managed["id"])
        if open_one:
            return {**ProposalService(self.app).render(open_one["id"]), "already_open": True}
        today = today or date.today()
        s = self.app.settings
        weekly = await WeeklyDiscoveries(self.app).weekly_playlist(managed, create=False)
        empty: dict[str, Any] = {
            "proposal_id": None,
            "liked": 0,
            "cap": 0,
            "items": [],
            "already_open": False,
        }
        if weekly is None:
            return {**empty, "notes": ["No weekly discoveries playlist yet."]}
        weekly_entries = await self.app.library.playlist_tracks(weekly.id)
        main_entries = await self.app.library.playlist_tracks(managed["apple_playlist_id"])
        in_main = {TrackRef.of(e).id for e in main_entries}
        rejected = {str(t["catalog_id"]) for t in self.proposals.rejected_tracks(500)}
        candidates = [e for e in weekly_entries if e.catalog_id and e.catalog_id not in in_main | rejected]
        added_on = self._batch_dates(managed["id"])
        windows: dict[date, ListeningWindow] = {}
        listening = ListeningRepository(self.app.db)
        try:
            ratings = await self.app.taste.ratings([e.catalog_id for e in candidates if e.catalog_id])
        except AppleMusicError:
            ratings = {}
        liked: list[tuple[tuple[int, int, float], Any, str, Delta | None]] = []
        for e in candidates:
            since = added_on.get(str(e.catalog_id), today - timedelta(days=7))
            if since not in windows:  # a same-day reading counts: start the day before the batch
                windows[since] = listening.window(since - timedelta(days=1), today)
            delta = windows[since].deltas.get(match_key(e.artist, e.name))
            why = is_liked(
                delta, ratings.get(str(e.catalog_id)) == 1, s.promote_min_plays, s.promote_max_skip_ratio
            )
            if why:
                loved = why == "loved"
                rank = (
                    0 if loved else 1,
                    -(delta.plays if delta else 0),
                    (delta.skip_ratio or 0) if delta else 0.0,
                )
                liked.append((rank, e, why, delta))
        liked.sort(key=lambda r: r[0])
        cap = promotion_cap(
            len(main_entries),
            self.promoted_this_week(managed["id"]),
            s.promote_weekly_fraction,
            s.promote_weekly_max,
        )
        chosen = liked[:cap]
        notes = []
        if len(liked) > cap:
            notes.append(f"{len(liked) - cap} more liked track(s) wait for next week (weekly cap {cap}).")
        if not chosen:
            return {**empty, "liked": len(liked), "cap": cap, "notes": notes or ["Nothing liked yet."]}
        evidence = {
            str(e.catalog_id): {"why": why, **({"plays": d.plays, "skips": d.skips} if d else {})}
            for _, e, why, d in chosen
        }
        proposal_id = self.proposals.create(
            "promotion",
            [
                ProposedTrack(
                    TrackInfo(
                        title=e.name, artist_name=e.artist, catalog_id=e.catalog_id, duration_ms=e.duration_ms
                    ),
                    action="add",
                )
                for _, e, _, _ in chosen
            ],
            job_run_id=job_run_id,
            managed_playlist_id=managed["id"],
            params={
                "playlist_id": managed["apple_playlist_id"],
                "playlist_name": managed["name"],
                "weekly_playlist_id": weekly.id,
                "purpose": managed.get("profile") or managed.get("purpose"),
                "cap": cap,
                "liked": len(liked),
                "evidence": evidence,
                "notes": notes,
            },
        )
        rendered = ProposalService(self.app).render(proposal_id)
        return {**rendered, "cap": cap, "liked": len(liked), "already_open": False}

    async def apply(
        self,
        proposal_id: int,
        *,
        purpose: str | None = None,
        estimates: list[dict[str, Any]] | None = None,
        allow_rebuild: bool = False,
    ) -> dict[str, Any]:
        from .operations import insert_into_playlist

        p = self.proposals.get(proposal_id)
        if p is None or p["kind"] != "promotion" or p["status"] != "approved":
            raise ValueError(f"proposal {proposal_id} is not an approved promotion")
        params = p["params"] or {}
        managed = self.managed.get_by_id(p["managed_playlist_id"]) if p["managed_playlist_id"] else None
        playlist_id = managed["apple_playlist_id"] if managed else params["playlist_id"]
        accepted = [i for i in p["items"] if i["decision"] == "accepted" and i["action"] == "add"]
        given = {str(t["id"]): t for t in estimates or []}
        new_tracks = [
            {
                **given.get(str(i["catalog_id"]), {}),
                "id": str(i["catalog_id"]),
                "artist": i["artist_name"],
                "title": i["title"],
            }
            for i in accepted
        ]
        new_ids = {t["id"] for t in new_tracks}
        result: dict[str, Any] = {"proposal_id": proposal_id, "status": "applied", "warnings": []}
        if new_tracks:
            inserted = await insert_into_playlist(
                self.app,
                playlist_id,
                purpose or params.get("purpose") or "",
                new_tracks,
                [t for t in estimates or [] if str(t["id"]) not in new_ids],
                allow_rebuild,
                origin="promotion",
                proposal_id=proposal_id,
            )
            result["promoted"] = inserted
            if (inserted.get("edit") or {}).get("status") == "needs_decision":
                result["status"] = "needs_decision"
                return result
            weekly_id = params.get("weekly_playlist_id")
            if weekly_id:
                try:
                    keep = [
                        TrackRef.of(e).id
                        for e in await self.app.library.playlist_tracks(weekly_id)
                        if TrackRef.of(e).id not in new_ids
                    ]
                    result["weekly_cleanup"] = (await self.app.editor.sync(weekly_id, keep)).status
                except AppleMusicError as exc:
                    result["warnings"].append(f"Promoted tracks are still in the weekly playlist: {exc}")
        self.proposals.set_status(proposal_id, "applied")
        return result
