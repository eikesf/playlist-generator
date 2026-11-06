"""What "known" means (guide §7.4, owner's definitions, all configurable):

* **Known track**: loved (Apple Music rating or Music app favorite) OR played ``KNOWN_MIN_PLAYS``
  (default 10) times or more.
* **Never heard**: 0 plays and not in the library.
* **Partly known**: in the library or played 1-9 times, and not loved. Not new, not known: left out
  of both groups by default.
* **Known artist**: has at least one known track.

Play counts come from the daily Music app snapshots (``play_snapshot``). Until the first snapshot
exists (or off macOS) the definitions fall back to "in the library or recently played = known" and
the result says so (``definitions: "fallback"``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..db.repositories import Counters, match_key
from ..text import artist_names, primary_artist

KNOWN_TRACK = "known_track"
PARTLY_KNOWN = "partly_known"
KNOWN_ARTIST_NEW_TRACK = "known_artist_new_track"
NEW_ARTIST = "new_artist"


@dataclass
class ListeningFacts:
    counters: dict[str, Counters] = field(default_factory=dict)  # match_key -> Music app counters
    loved_ids: set[str] = field(default_factory=set)  # catalog ids rated "love"
    disliked_ids: set[str] = field(default_factory=set)
    library_ids: set[str] = field(default_factory=set)
    library_keys: set[str] = field(default_factory=set)
    recent_ids: set[str] = field(default_factory=set)
    recent_keys: set[str] = field(default_factory=set)
    known_artist_names: set[str] = field(default_factory=set)  # fallback: artists the user listens to
    min_plays: int = 10

    @property
    def has_play_data(self) -> bool:
        return bool(self.counters)


class FamiliarityIndex:
    def __init__(self, facts: ListeningFacts):
        self.facts = facts
        self.definitions = "guide" if facts.has_play_data else "fallback"
        self._known_artists = self._compute_known_artists()

    def _compute_known_artists(self) -> set[str]:
        f = self.facts
        if not f.has_play_data:
            return {n for a in f.known_artist_names for n in artist_names(a)}
        artists = set()
        for key, c in f.counters.items():
            if c.favorited or c.played >= f.min_plays:
                artists.add(key.split("|", 1)[0])
        return artists

    def plays(self, artist: str, title: str) -> int | None:
        c = self.facts.counters.get(match_key(artist, title))
        return c.played if c else None

    def track_status(self, song_id: str, artist: str, title: str) -> str:
        """known_track, partly_known, or "unheard"."""
        f = self.facts
        key = match_key(artist, title)
        if not f.has_play_data:
            heard = song_id in f.library_ids or song_id in f.recent_ids or key in f.library_keys
            return KNOWN_TRACK if heard or song_id in f.loved_ids else "unheard"
        c = f.counters.get(key)
        if song_id in f.loved_ids or (c is not None and (c.favorited or c.played >= f.min_plays)):
            return KNOWN_TRACK
        in_library = song_id in f.library_ids or key in f.library_keys
        if (c is not None and c.played > 0) or in_library or song_id in f.recent_ids or key in f.recent_keys:
            return PARTLY_KNOWN
        return "unheard"

    def knows_artist(self, artist: str) -> bool:
        names = set(artist_names(artist)) | {primary_artist(artist)}
        return bool(names & self._known_artists)

    def bucket(self, song_id: str, artist: str, title: str) -> str:
        status = self.track_status(song_id, artist, title)
        if status != "unheard":
            return status
        return KNOWN_ARTIST_NEW_TRACK if self.knows_artist(artist) else NEW_ARTIST

    def is_disliked(self, song_id: str, artist: str, title: str) -> bool:
        c = self.facts.counters.get(match_key(artist, title))
        return song_id in self.facts.disliked_ids or bool(c and c.disliked)
