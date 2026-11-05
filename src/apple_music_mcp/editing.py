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

    def to_dict(self) -> dict[str, Any]:
        return {
            "playlist": self.playlist.to_dict(),
            "strategy": self.strategy,
            "changed": self.changed,
            "removed": self.removed,
            "added": self.added,
            "verified_order": self.verified,
            "warnings": self.warnings,
        }


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

    async def sync(self, playlist_id: str, desired_ids: list[str], allow_rebuild: bool = True) -> EditResult:
        """Make the playlist contain exactly ``desired_ids`` in that order."""
        desired = dedupe([TrackRef.parse(i) for i in desired_ids if str(i).strip()])
        playlist = await self.library.get_playlist(playlist_id)
        current = await self.library.playlist_tracks(playlist_id)
        if [TrackRef.of(e) for e in current] == desired:
            return EditResult(playlist, "none", changed=False, verified=True)

        last_error: Exception | None = None
        for strategy in self._ordered(allow_rebuild):
            try:
                result = await strategy.apply(self.library, playlist, current, desired)
            except StrategyUnsupported as exc:
                last_error = exc
                continue
            self.state.set(STATE_KEY, strategy.name)
            final = [TrackRef.of(e) for e in await self.library.playlist_tracks(result.playlist.id)]
            result.verified = final == desired
            if not result.verified:
                result.warnings.append(
                    "Read-back order differs from the requested order; Apple may still be syncing."
                )
            return result
        raise AppleMusicError(
            "This account does not accept in-place playlist edits"
            + ("" if allow_rebuild else " and rebuild was not allowed")
            + f": {last_error}"
        )

    def _ordered(self, allow_rebuild: bool) -> list[EditStrategy]:
        strategies = [s for s in self.strategies if allow_rebuild or s.name != RebuildStrategy.name]
        preferred = self.state.get(STATE_KEY)
        if preferred == RebuildStrategy.name and allow_rebuild:
            strategies.sort(key=lambda s: s.name != RebuildStrategy.name)
        return strategies

    async def current_ids(self, playlist_id: str) -> list[str]:
        return [TrackRef.of(e).id for e in await self.library.playlist_tracks(playlist_id)]
