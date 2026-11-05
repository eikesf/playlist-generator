"""Make an existing library playlist match a desired track order (reorder / add / remove).

Apple's web API has no "move track" or "replace track list" endpoint. It can add tracks (append),
remove a song from a playlist (every occurrence) and create/rename/delete playlists. On top of
those we use two strategies (Strategy pattern), tried in order:

``InPlaceStrategy``
    Keeps the same playlist (same link). Finds the longest common prefix with the desired order,
    removes the tail and re-appends it in the desired order. Snapshot first; if anything fails
    midway, the original track list is restored.

``RebuildStrategy``
    Creates a new playlist with the same name and description in the desired order, verifies it,
    then deletes the old one (or renames it to "<name> (old)" if deletion is refused). The link
    changes, so it is the fallback when in-place editing is not accepted by the account.

The strategy that works is remembered in the local state file so later edits go straight to it.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from .apple.errors import AppleMusicError, AuthError
from .apple.library import LibraryService
from .config import StateStore
from .models import Playlist, PlaylistEntry

STATE_KEY = "playlist_edit_strategy"
UNSUPPORTED_STATUSES = (400, 403, 404, 405, 501)


class StrategyUnsupported(Exception):
    """The strategy cannot run on this account; nothing was changed."""


@dataclass(frozen=True)
class TrackRef:
    id: str
    kind: str  # "songs" (catalog) or "library-songs"

    @classmethod
    def parse(cls, value: str) -> TrackRef:
        value = str(value).strip()
        return cls(value, "library-songs" if value.startswith("i.") else "songs")

    @classmethod
    def of(cls, entry: PlaylistEntry) -> TrackRef:
        sid, kind = entry.reference
        return cls(sid, kind)


@dataclass
class EditResult:
    playlist: Playlist
    strategy: str
    changed: bool
    removed: int = 0
    added: int = 0
    verified: bool = False
    warnings: list[str] = field(default_factory=list)
    status: str = "applied"  # applied | unchanged | needs_decision
    decision: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "status": self.status,
            "playlist": self.playlist.to_dict(),
            "strategy": self.strategy,
            "changed": self.changed,
            "removed": self.removed,
            "added": self.added,
            "verified_order": self.verified,
            "warnings": self.warnings,
        }
        if self.decision is not None:
            data["decision"] = self.decision
        return data


def dedupe(refs: list[TrackRef]) -> list[TrackRef]:
    return list(dict.fromkeys(refs))


async def add_refs(library: LibraryService, playlist_id: str, refs: list[TrackRef]) -> int:
    """Append refs preserving order, batching consecutive refs of the same kind."""
    added, batch = 0, []
    for ref in refs:
        if batch and ref.kind != batch[0].kind:
            added += await library.add_tracks(playlist_id, [r.id for r in batch], batch[0].kind)
            batch = []
        batch.append(ref)
    if batch:
        added += await library.add_tracks(playlist_id, [r.id for r in batch], batch[0].kind)
    return added


class EditStrategy(Protocol):
    name: str

    async def apply(
        self,
        library: LibraryService,
        playlist: Playlist,
        current: list[PlaylistEntry],
        desired: list[TrackRef],
    ) -> EditResult: ...


class InPlaceStrategy:
    name = "in_place"

    async def apply(
        self,
        library: LibraryService,
        playlist: Playlist,
        current: list[PlaylistEntry],
        desired: list[TrackRef],
    ) -> EditResult:
        current_refs = [TrackRef.of(e) for e in current]
        k = 0
        while k < min(len(current_refs), len(desired)) and current_refs[k] == desired[k]:
            k += 1
        tail = current[k:]
        # Removal deletes every occurrence of a song; if a tail song also sits in the kept prefix,
        # keeping the prefix is impossible, so rewrite everything.
        prefix_ids = {e.library_id for e in current[:k]}
        if any(e.library_id in prefix_ids for e in tail):
            k, tail = 0, current
        to_remove = list(dict.fromkeys(e.library_id for e in tail))
        to_add = desired[k:]

        removed = 0
        try:
            for library_id in to_remove:
                try:
                    await library.remove_entry(playlist.id, library_id)
                except AppleMusicError as exc:
                    if removed == 0 and not isinstance(exc, AuthError) and exc.status in UNSUPPORTED_STATUSES:
                        raise StrategyUnsupported(str(exc)) from exc
                    raise
                removed += 1
            added = await add_refs(library, playlist.id, to_add)
        except StrategyUnsupported:
            raise
        except Exception as exc:
            restored = await self._restore(library, playlist.id, current)
            raise AppleMusicError(
                f"In-place edit failed midway ({exc}); original order "
                + ("restored." if restored else "could NOT be fully restored, check the playlist."),
                getattr(exc, "status", None),
            ) from exc
        return EditResult(
            playlist, self.name, changed=bool(to_remove or to_add), removed=removed, added=added
        )

    @staticmethod
    async def _restore(library: LibraryService, playlist_id: str, snapshot: list[PlaylistEntry]) -> bool:
        try:
            for entry in {e.library_id: e for e in await library.playlist_tracks(playlist_id)}.values():
                await library.remove_entry(playlist_id, entry.library_id)
            await add_refs(library, playlist_id, [TrackRef.of(e) for e in snapshot])
            return True
        except Exception:  # noqa: BLE001 - best effort
            return False


class RebuildStrategy:
    name = "rebuild"

    async def apply(
        self,
        library: LibraryService,
        playlist: Playlist,
        current: list[PlaylistEntry],
        desired: list[TrackRef],
    ) -> EditResult:
        new = await library.create_playlist(playlist.name, playlist.description)
        added = await add_refs(library, new.id, desired)
        check = [TrackRef.of(e) for e in await library.playlist_tracks(new.id)]
        if check != desired:
            await _quietly(library.delete_playlist(new.id))
            raise AppleMusicError(
                "Rebuilt playlist did not match the desired order; original left untouched."
            )
        warnings = [
            f"Apple did not allow editing in place, so the playlist was recreated (new link: {new.url})."
        ]
        try:
            await library.delete_playlist(playlist.id)
        except AppleMusicError:
            try:
                await library.update_playlist(playlist.id, name=f"{playlist.name} (old)")
                warnings.append(
                    f"The old copy was renamed to '{playlist.name} (old)'; delete it in the Music app."
                )
            except AppleMusicError:
                warnings.append(f"The old copy '{playlist.name}' still exists; delete it in the Music app.")
        return EditResult(new, self.name, changed=True, removed=len(current), added=added, warnings=warnings)


async def _quietly(coro: Any) -> None:
    with contextlib.suppress(Exception):
        await coro


SMALL_CHANGE_LIMIT = 5


def _label(entry: PlaylistEntry) -> str:
    return f"{entry.artist} - {entry.name}".strip(" -") or entry.library_id


def _longest_increasing(seq: list[int]) -> set[int]:
    """Indices (into ``seq``) of one longest strictly increasing subsequence (patience sorting)."""
    import bisect

    tails: list[int] = []
    tails_idx: list[int] = []
    prev = [-1] * len(seq)
    for i, value in enumerate(seq):
        k = bisect.bisect_left(tails, value)
        if k == len(tails):
            tails.append(value)
            tails_idx.append(i)
        else:
            tails[k] = value
            tails_idx[k] = i
        prev[i] = tails_idx[k - 1] if k else -1
    keep: set[int] = set()
    i = tails_idx[-1] if tails_idx else -1
    while i != -1:
        keep.add(i)
        i = prev[i]
    return keep


def manual_plan(current: list[PlaylistEntry], desired: list[TrackRef]) -> dict[str, Any]:
    """Minimal human steps to turn ``current`` into ``desired`` in the Music app.

    Tracks on a longest increasing subsequence stay put; every other kept track is moved right
    after its predecessor in the desired order. Tracks not in ``desired`` are removed.
    """
    by_ref = {TrackRef.of(e): e for e in current}
    position = {TrackRef.of(e): i for i, e in enumerate(current)}
    wanted = set(desired)
    steps: list[str] = []
    removals = [e for e in current if TrackRef.of(e) not in wanted]
    steps += [f"Remover '{_label(e)}'" for e in removals]
    kept = [r for r in desired if r in position]
    stay = _longest_increasing([position[r] for r in kept])
    moves = 0
    for k, ref in enumerate(kept):
        if k in stay:
            continue
        moves += 1
        target = by_ref[ref]
        if k == 0:
            steps.append(f"Mover '{_label(target)}' para o início")
        else:
            steps.append(f"Mover '{_label(target)}' para logo depois de '{_label(by_ref[kept[k - 1]])}'")
    missing = [r.id for r in desired if r not in position]
    changes = len(removals) + moves + len(missing)
    size = "small" if changes <= SMALL_CHANGE_LIMIT else "large"
    return {
        "changes": changes,
        "size": size,
        "removals": len(removals),
        "moves": moves,
        "manual_steps": steps,
        "not_in_playlist": missing,
        "recommendation": (
            "manual: few changes, do them in the Music app with the steps above"
            if size == "small"
            else "rebuild: many changes; recreate the playlist (same name and order, new link)"
        ),
    }


class PlaylistEditor:
    def __init__(
        self,
        library: LibraryService,
        state: StateStore,
        strategies: list[EditStrategy] | None = None,
    ):
        self.library = library
        self.state = state
        self.strategies = strategies or [InPlaceStrategy(), RebuildStrategy()]

    async def sync(self, playlist_id: str, desired_ids: list[str], allow_rebuild: bool = False) -> EditResult:
        """Make the playlist contain exactly ``desired_ids`` in that order.

        In-place editing is tried first. Recreating the playlist (``RebuildStrategy``) only happens
        with ``allow_rebuild=True``, i.e. after the user confirmed it. Without confirmation, a refused
        in-place edit returns ``status="needs_decision"`` with the manual steps and a recommendation;
        new tracks are still appended at the end (that never destroys anything).
        """
        desired = dedupe([TrackRef.parse(i) for i in desired_ids if str(i).strip()])
        playlist = await self.library.get_playlist(playlist_id)
        current = await self.library.playlist_tracks(playlist_id)
        if [TrackRef.of(e) for e in current] == desired:
            return EditResult(playlist, "none", changed=False, verified=True, status="unchanged")

        for strategy in self.strategies:
            if strategy.name == RebuildStrategy.name and not allow_rebuild:
                return await self._needs_decision(playlist, current, desired)
            try:
                result = await strategy.apply(self.library, playlist, current, desired)
            except StrategyUnsupported:
                continue
            self.state.set(STATE_KEY, strategy.name)
            final = [TrackRef.of(e) for e in await self.library.playlist_tracks(result.playlist.id)]
            result.verified = final == desired
            if not result.verified:
                result.warnings.append(
                    "Read-back order differs from the requested order; Apple may still be syncing."
                )
            return result
        raise AppleMusicError("No playlist edit strategy could be applied on this account.")

    async def _needs_decision(
        self, playlist: Playlist, current: list[PlaylistEntry], desired: list[TrackRef]
    ) -> EditResult:
        self.state.set(STATE_KEY, "in_place_refused")
        present = {TrackRef.of(e) for e in current}
        new = [r for r in desired if r not in present]
        added = await add_refs(self.library, playlist.id, new) if new else 0
        if added:
            current = await self.library.playlist_tracks(playlist.id)
        decision = manual_plan(current, desired)
        warnings = ["Apple refused editing this playlist in place. Nothing was removed or reordered."]
        if added:
            warnings.append(f"{added} new track(s) were appended at the end so you can move them by hand.")
        return EditResult(
            playlist,
            "none",
            changed=bool(added),
            added=added,
            status="needs_decision",
            decision=decision,
            warnings=warnings,
        )

    async def current_ids(self, playlist_id: str) -> list[str]:
        return [TrackRef.of(e).id for e in await self.library.playlist_tracks(playlist_id)]
