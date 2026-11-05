"""Text normalization shared by matching, artist lookup and sequencing."""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Lowercase, accent-free, punctuation-free form used for fuzzy comparisons."""
    text = strip_accents(text).lower().replace("&", " and ").replace("$", "s")
    return _NON_ALNUM_RE.sub(" ", text).strip()


def artist_names(artist: str) -> list[str]:
    """Split a credit like "A, B & C feat. D" into normalized names (primary first)."""
    parts = re.split(r",|&| and | x | feat\.? | ft\.? | featuring | vs\.? ", strip_accents(artist).lower())
    return [normalize(p) for p in parts if normalize(p)]


def primary_artist(artist: str) -> str:
    names = artist_names(artist)
    return names[0] if names else normalize(artist)
