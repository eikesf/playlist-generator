"""Turn a purpose + size into a concrete plan: minutes, tracks and targets per act."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .profiles import Profile

DEFAULT_TRACK_COUNT = 75
MIN_TRACKS = 1


@dataclass
class ActPlan:
    name: str
    intent: str
    minutes: float
    tracks: int
    energy: tuple[float, float]
    bpm: tuple[int, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "intent": self.intent,
            "minutes": round(self.minutes, 1),
            "tracks": self.tracks,
            "energy": [round(self.energy[0], 1), round(self.energy[1], 1)],
            "target_bpm": list(self.bpm),
        }


@dataclass
class PlaylistPlan:
    profile: Profile
    total_minutes: float
    total_tracks: int
    acts: list[ActPlan]

    def to_dict(self) -> dict[str, Any]:
        p = self.profile
        return {
            "purpose": p.key,
            "label": p.label,
            "description": p.description,
            "total_minutes": round(self.total_minutes),
            "total_tracks": self.total_tracks,
            "avg_track_minutes": p.avg_track_minutes,
            "max_track_minutes": p.max_track_minutes,
            "vocals": p.vocals,
            "bpm_range": list(p.bpm_range),
            "transition_rules": {
                "max_bpm_step": p.max_bpm_step,
                "max_energy_step": p.max_energy_step,
                "min_tracks_between_same_artist": p.min_artist_gap,
            },
            "guidance": list(p.guidance),
            "acts": [a.to_dict() for a in self.acts],
        }


def distribute(total: int, shares: list[float], minimum: int = MIN_TRACKS) -> list[int]:
    """Split ``total`` into integers proportional to ``shares`` (largest remainder), each >= minimum."""
    if total < minimum * len(shares):
        total = minimum * len(shares)
    raw = [total * s for s in shares]
    counts = [max(minimum, int(r)) for r in raw]
    while sum(counts) > total:
        i = max((i for i in range(len(counts)) if counts[i] > minimum), key=lambda i: counts[i] - raw[i])
        counts[i] -= 1
    order = sorted(range(len(shares)), key=lambda i: raw[i] - counts[i], reverse=True)
    k = 0
    while sum(counts) < total:
        counts[order[k % len(order)]] += 1
        k += 1
    return counts


def plan_playlist(
    profile: Profile,
    duration_minutes: float | None = None,
    track_count: int | None = None,
    avg_track_minutes: float | None = None,
) -> PlaylistPlan:
    avg = avg_track_minutes or profile.avg_track_minutes
    if track_count and not duration_minutes:
        duration_minutes = track_count * avg
    elif duration_minutes and not track_count:
        track_count = max(len(profile.acts), round(duration_minutes / avg))
    elif not duration_minutes and not track_count:
        track_count = DEFAULT_TRACK_COUNT
        duration_minutes = track_count * avg
    assert duration_minutes is not None and track_count is not None
    counts = distribute(track_count, [a.share for a in profile.acts])
    acts = [
        ActPlan(
            name=act.name,
            intent=act.intent,
            minutes=duration_minutes * act.share,
            tracks=n,
            energy=(act.energy_start, act.energy_end),
            bpm=(
                round(profile.bpm_for_energy(min(act.energy_start, act.energy_end))),
                round(profile.bpm_for_energy(max(act.energy_start, act.energy_end))),
            ),
        )
        for act, n in zip(profile.acts, counts, strict=True)
    ]
    return PlaylistPlan(profile, duration_minutes, sum(counts), acts)
