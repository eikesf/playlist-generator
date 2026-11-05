"""Plain data models shared by the client, resolver and MCP tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Song:
    id: str
    name: str
    artist: str
    album: str | None = None
    duration_ms: int | None = None
    genres: list[str] = field(default_factory=list)
    release_date: str | None = None
    url: str | None = None
    isrc: str | None = None
    explicit: bool = False
    source: str = "apple-music"

    @property
    def label(self) -> str:
        return f"{self.artist} - {self.name}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["duration"] = format_duration(self.duration_ms)
        return data

    @classmethod
    def from_amp(cls, item: dict[str, Any]) -> Song:
        """Build from an amp-api catalog song resource."""
        attrs = item.get("attributes") or {}
        return cls(
            id=str(item.get("id")),
            name=attrs.get("name", ""),
            artist=attrs.get("artistName", ""),
            album=attrs.get("albumName"),
            duration_ms=attrs.get("durationInMillis"),
            genres=[g for g in attrs.get("genreNames") or [] if g != "Music"],
            release_date=attrs.get("releaseDate"),
            url=attrs.get("url"),
            isrc=attrs.get("isrc"),
            explicit=attrs.get("contentRating") == "explicit",
            source="apple-music",
        )

    @classmethod
    def from_itunes(cls, item: dict[str, Any]) -> Song:
        """Build from an iTunes Search API result (trackId == Apple Music catalog id)."""
        genre = item.get("primaryGenreName")
        return cls(
            id=str(item.get("trackId")),
            name=item.get("trackName", ""),
            artist=item.get("artistName", ""),
            album=item.get("collectionName"),
            duration_ms=item.get("trackTimeMillis"),
            genres=[genre] if genre else [],
            release_date=(item.get("releaseDate") or "")[:10] or None,
            url=item.get("trackViewUrl"),
            explicit=item.get("trackExplicitness") == "explicit",
            source="itunes-search",
        )


@dataclass
class Artist:
    id: str
    name: str
    genres: list[str] = field(default_factory=list)
    url: str | None = None

    @classmethod
    def from_amp(cls, item: dict[str, Any]) -> Artist:
        attrs = item.get("attributes") or {}
        return cls(
            id=str(item.get("id")),
            name=attrs.get("name", ""),
            genres=list(attrs.get("genreNames") or []),
            url=attrs.get("url"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Playlist:
    id: str
    name: str
    description: str | None = None
    can_edit: bool | None = None
    date_added: str | None = None

    @property
    def url(self) -> str:
        return f"https://music.apple.com/library/playlist/{self.id}"

    @classmethod
    def from_amp(cls, item: dict[str, Any]) -> Playlist:
        attrs = item.get("attributes") or {}
        desc = attrs.get("description")
        if isinstance(desc, dict):
            desc = desc.get("standard") or desc.get("short")
        return cls(
            id=str(item.get("id")),
            name=attrs.get("name", ""),
            description=desc,
            can_edit=attrs.get("canEdit"),
            date_added=attrs.get("dateAdded"),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["url"] = self.url
        return data


@dataclass
class PlaylistEntry:
    """A track as it sits in a library playlist."""

    library_id: str
    catalog_id: str | None
    name: str
    artist: str
    duration_ms: int | None = None

    @property
    def reference(self) -> tuple[str, str]:
        """(id, type) to re-add this entry: catalog song if known, else the library song."""
        return (self.catalog_id, "songs") if self.catalog_id else (self.library_id, "library-songs")

    @classmethod
    def from_amp(cls, item: dict[str, Any]) -> PlaylistEntry:
        attrs = item.get("attributes") or {}
        play = attrs.get("playParams") or {}
        return cls(
            library_id=str(item.get("id")),
            catalog_id=play.get("catalogId") or None,
            name=attrs.get("name", ""),
            artist=attrs.get("artistName", ""),
            duration_ms=attrs.get("durationInMillis"),
        )

    def to_dict(self) -> dict[str, Any]:
        data = {"id": self.reference[0], **asdict(self)}
        data["duration"] = format_duration(self.duration_ms)
        return data


def format_duration(ms: int | None) -> str | None:
    if ms is None:
        return None
    total = round(ms / 1000)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
