"""User library operations (``/v1/me``): account storefront and library playlists."""

from __future__ import annotations

from typing import Any

from ..models import Playlist, PlaylistEntry
from .errors import AppleMusicError
from .transport import AmpTransport

ADD_TRACKS_CHUNK = 100
PLAYLISTS = "/v1/me/library/playlists"


class LibraryService:
    def __init__(self, transport: AmpTransport, add_chunk: int = ADD_TRACKS_CHUNK):
        self._amp = transport
        self.add_chunk = add_chunk
        self._remove_variant: str | None = None  # learned at runtime, see remove_entry

    @property
    def remove_variant(self) -> str | None:
        """Track-removal request shape this account accepted ("query" / "path"), once known."""
        return self._remove_variant

    async def storefront(self) -> str:
        data = await self._amp.request("GET", "/v1/me/storefront", user=True)
        items = (data or {}).get("data") or []
        if not items:
            raise AppleMusicError("Apple Music returned no storefront for this account")
        return str(items[0].get("id"))

    # ---------------------------------------------------------------- playlists
    async def list_playlists(self, limit: int = 100) -> list[Playlist]:
        return [
            Playlist.from_amp(item)
            async for item in self._amp.paginate(PLAYLISTS, params={"limit": 100}, user=True, limit=limit)
        ]

    async def get_playlist(self, playlist_id: str) -> Playlist:
        data = await self._amp.request("GET", f"{PLAYLISTS}/{playlist_id}", user=True)
        items = (data or {}).get("data") or []
        if not items:
            raise AppleMusicError(f"Playlist {playlist_id} not found", 404)
        return Playlist.from_amp(items[0])

    async def create_playlist(self, name: str, description: str | None = None) -> Playlist:
        attributes: dict[str, Any] = {"name": name}
        if description:
            attributes["description"] = description
        data = await self._amp.request(
            "POST",
            PLAYLISTS,
            json={"attributes": attributes, "relationships": {"tracks": {"data": []}}},
            user=True,
        )
        items = (data or {}).get("data") or []
        if not items:
            raise AppleMusicError("Apple Music did not return the created playlist")
        return Playlist.from_amp(items[0])

    async def update_playlist(
        self, playlist_id: str, name: str | None = None, description: str | None = None
    ) -> None:
        attributes = {k: v for k, v in (("name", name), ("description", description)) if v is not None}
        if attributes:
            await self._amp.request(
                "PATCH", f"{PLAYLISTS}/{playlist_id}", json={"attributes": attributes}, user=True
            )

    async def delete_playlist(self, playlist_id: str) -> None:
        await self._amp.request("DELETE", f"{PLAYLISTS}/{playlist_id}", user=True)

    # ------------------------------------------------------------------- tracks
    async def add_tracks(self, playlist_id: str, song_ids: list[str], kind: str = "songs") -> int:
        """Append tracks in order, in chunks. ``kind`` is ``songs`` (catalog) or ``library-songs``."""
        sent = 0
        for i in range(0, len(song_ids), self.add_chunk):
            chunk = song_ids[i : i + self.add_chunk]
            await self._amp.request(
                "POST",
                f"{PLAYLISTS}/{playlist_id}/tracks",
                json={"data": [{"id": sid, "type": kind} for sid in chunk]},
                user=True,
            )
            sent += len(chunk)
        return sent

    async def add_entries(self, playlist_id: str, entries: list[PlaylistEntry]) -> int:
        """Re-add entries preserving order; catalog id when known, library id otherwise."""
        sent = 0
        batch: list[str] = []
        batch_kind: str | None = None
        for entry in entries:
            sid, kind = entry.reference
            if batch and kind != batch_kind:
                sent += await self.add_tracks(playlist_id, batch, batch_kind or "songs")
                batch = []
            batch.append(sid)
            batch_kind = kind
        if batch:
            sent += await self.add_tracks(playlist_id, batch, batch_kind or "songs")
        return sent

    async def playlist_tracks(self, playlist_id: str) -> list[PlaylistEntry]:
        return [
            PlaylistEntry.from_amp(item)
            async for item in self._amp.paginate(
                f"{PLAYLISTS}/{playlist_id}/tracks", params={"limit": 100}, user=True
            )
        ]

    async def remove_entry(self, playlist_id: str, library_id: str) -> None:
        """Remove a track (all its occurrences) from a playlist.

        Two request shapes are used by open-source web-API clients; we try both and remember the
        one this account accepts.
        """
        variants = ["query", "path"]
        if self._remove_variant:
            variants = [self._remove_variant]
        last: AppleMusicError | None = None
        for variant in variants:
            try:
                if variant == "query":
                    await self._amp.request(
                        "DELETE",
                        f"{PLAYLISTS}/{playlist_id}/tracks",
                        params={"ids[library-songs]": library_id, "mode": "all"},
                        user=True,
                    )
                else:
                    await self._amp.request(
                        "DELETE", f"{PLAYLISTS}/{playlist_id}/tracks/{library_id}", user=True
                    )
                self._remove_variant = variant
                return
            except AppleMusicError as exc:
                if exc.status not in (400, 404, 405):
                    raise
                last = exc
        assert last is not None
        raise last
