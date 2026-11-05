"""Familiarity mix: how much of a playlist the user already knows, at two separate levels.

Knowing an artist is not knowing all their songs, so a track falls in one of three buckets:

* ``known_track``             - the user already knows this song (library / recently played)
* ``known_artist_new_track``  - new song by an artist the user listens to (a "deep cut")
* ``new_artist``              - a discovery: the user does not know the artist yet

Two targets drive the mix: ``known_tracks_pct`` (share of known songs) and ``known_artists_pct``
(share of tracks by known artists, which includes the known songs).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

BUCKETS = ("known_track", "known_artist_new_track", "new_artist")
LABELS = {
    "known_track": "músicas que você já conhece",
    "known_artist_new_track": "músicas novas de artistas que você conhece",
    "new_artist": "artistas novos",
}


@dataclass(frozen=True)
class MixTargets:
    known_tracks_pct: float
    known_artists_pct: float

    def normalized(self) -> tuple[MixTargets, list[str]]:
        warnings = []
        kt = min(100.0, max(0.0, self.known_tracks_pct))
        ka = min(100.0, max(0.0, self.known_artists_pct))
        if ka < kt:
            warnings.append(
                f"known_artists_pct ({ka:g}%) cannot be lower than known_tracks_pct ({kt:g}%): every known "
                f"song is by a known artist. Using {kt:g}% for both."
            )
            ka = kt
        return MixTargets(kt, ka), warnings


def mix_counts(total: int, targets: MixTargets) -> dict[str, Any]:
    """Number of tracks per bucket for a playlist of ``total`` tracks."""
    t, warnings = targets.normalized()
    known_tracks = round(total * t.known_tracks_pct / 100)
    known_artist_tracks = max(known_tracks, round(total * t.known_artists_pct / 100))
    counts = {
        "known_track": known_tracks,
        "known_artist_new_track": known_artist_tracks - known_tracks,
        "new_artist": total - known_artist_tracks,
    }
    return {
        "known_tracks_pct": t.known_tracks_pct,
        "known_artists_pct": t.known_artists_pct,
        "total_tracks": total,
        "counts": counts,
        "description": {b: f"{counts[b]} {LABELS[b]}" for b in BUCKETS},
        "warnings": warnings,
    }


def mix_check(actual: dict[str, int], targets: MixTargets, tolerance: int | None = None) -> dict[str, Any]:
    """Compare the bucket counts of a draft with the targets and say what to swap."""
    total = sum(actual.get(b, 0) for b in BUCKETS)
    plan = mix_counts(total, targets)
    target = plan["counts"]
    tol = tolerance if tolerance is not None else round(total * 0.05)  # 0 for small, 4 for 75 tracks
    delta = {b: actual.get(b, 0) - target[b] for b in BUCKETS}
    suggestions = []
    surplus = [b for b in BUCKETS if delta[b] > tol]
    deficit = [b for b in BUCKETS if delta[b] < -tol]
    for need in deficit:
        for extra in surplus:
            n = min(-delta[need], delta[extra])
            if n > 0:
                suggestions.append(f"replace {n} '{extra}' track(s) with '{need}' track(s)")
    if not suggestions:
        for need in deficit:
            suggestions.append(f"add {-delta[need]} '{need}' track(s)")
        for extra in surplus:
            suggestions.append(f"remove {delta[extra]} '{extra}' track(s)")
    pct = {b: round(100 * actual.get(b, 0) / total) if total else 0 for b in BUCKETS}
    return {
        "target": target,
        "actual": {b: actual.get(b, 0) for b in BUCKETS},
        "actual_pct": pct,
        "actual_known_tracks_pct": pct["known_track"],
        "actual_known_artists_pct": pct["known_track"] + pct["known_artist_new_track"],
        "delta": delta,
        "within_tolerance": not (surplus or deficit),
        "tolerance_tracks": tol,
        "suggestions": suggestions,
        "warnings": plan["warnings"],
    }
