"""Purpose profiles. The purpose of a playlist decides its energy curve, tempo, track length and vocals.

Energy is on a 1-10 scale. Each act covers a share of the total duration and interpolates its
energy linearly from ``energy_start`` to ``energy_end``. Profiles are data: add one here and every
tool (plan, order, skill guidance) picks it up (open/closed principle).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..text import normalize


@dataclass(frozen=True)
class Act:
    name: str
    share: float
    energy_start: float
    energy_end: float
    intent: str

    def energy_at(self, fraction: float) -> float:
        fraction = min(1.0, max(0.0, fraction))
        return self.energy_start + (self.energy_end - self.energy_start) * fraction


@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    description: str
    acts: tuple[Act, ...]
    bpm_range: tuple[int, int]
    avg_track_minutes: float
    vocals: str
    max_bpm_step: int = 6
    max_energy_step: float = 2.0
    min_artist_gap: int = 2
    max_track_minutes: float | None = None
    guidance: tuple[str, ...] = ()
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        total = sum(a.share for a in self.acts)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"profile {self.key}: act shares sum to {total}, expected 1.0")

    def act_at(self, t: float) -> tuple[int, Act, float]:
        """Act index, act and position inside the act for a time fraction ``t`` in [0, 1]."""
        t = min(1.0, max(0.0, t))
        start = 0.0
        for i, act in enumerate(self.acts):
            end = start + act.share
            if t <= end or i == len(self.acts) - 1:
                return i, act, (t - start) / act.share if act.share else 0.0
            start = end
        raise AssertionError("unreachable")

    def energy_at(self, t: float) -> float:
        _, act, inner = self.act_at(t)
        return act.energy_at(inner)

    def bpm_for_energy(self, energy: float) -> float:
        lo, hi = self.bpm_range
        return lo + (hi - lo) * (min(10.0, max(1.0, energy)) - 1) / 9

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("aliases")
        return data


PROFILES: dict[str, Profile] = {
    p.key: p
    for p in (
        Profile(
            key="dj_set",
            label="DJ-style set (techno / house / trance)",
            description="Builds like a club set: atmospheric opening, groove, build, peak, cooldown.",
            acts=(
                Act("Atmospheric opening", 0.15, 3, 4, "deep, hypnotic, melodic, low intensity"),
                Act("Groove / warm-up", 0.20, 4, 6, "rolling grooves, steady kick, rising tension"),
                Act("Melodic build", 0.25, 6, 8, "big melodies and breakdowns, energy climbing"),
                Act("Peak", 0.25, 8.5, 10, "hardest and most recognisable tracks, highest BPM"),
                Act("Cooldown", 0.15, 7, 4, "melodic, emotional, decompressing finish"),
            ),
            bpm_range=(118, 140),
            avg_track_minutes=6.5,
            vocals="sparse or ethereal",
            max_bpm_step=4,
            max_energy_step=2.0,
            min_artist_gap=3,
            guidance=(
                "Prefer original/extended mixes over radio edits.",
                "Move BPM in small steps (about ±3-4 BPM between neighbours).",
                "Put the most recognisable tracks in the peak; end on a melodic or emotional track.",
            ),
            aliases=(
                "set",
                "dj set",
                "dj",
                "techno",
                "house",
                "trance",
                "melodic techno",
                "rave",
                "club",
                "balada",
            ),
        ),
        Profile(
            key="workout",
            label="Workout / gym",
            description="Short warm-up, long sustained high energy, short cooldown.",
            acts=(
                Act("Warm-up", 0.10, 5, 7, "upbeat, rhythmic, getting the body moving"),
                Act("Main session", 0.80, 8, 9, "sustained high energy, strong beat, no dips"),
                Act("Cooldown", 0.10, 6, 4, "release, slower, positive"),
            ),
            bpm_range=(120, 150),
            avg_track_minutes=3.8,
            vocals="any; motivating hooks work well",
            max_bpm_step=8,
            max_energy_step=2.0,
            min_artist_gap=2,
            max_track_minutes=6.0,
            guidance=("Avoid long intros and ambient breakdowns in the main session.",),
            aliases=("treino", "academia", "gym", "musculacao", "workout", "lifting", "crossfit", "hiit"),
        ),
        Profile(
            key="running",
            label="Running / cardio",
            description="Steady cadence-friendly tempo with a strong finish.",
            acts=(
                Act("Warm-up", 0.10, 5, 7, "easy pace"),
                Act("Steady run", 0.70, 7.5, 8, "constant tempo, locks to the stride"),
                Act("Final push", 0.10, 9, 10, "the hardest tracks for the last kilometres"),
                Act("Cooldown", 0.10, 6, 3, "walk it off"),
            ),
            bpm_range=(150, 180),
            avg_track_minutes=3.8,
            vocals="any",
            max_bpm_step=6,
            max_energy_step=2.0,
            min_artist_gap=2,
            max_track_minutes=6.0,
            guidance=("Songs at half time (75-90 BPM) also lock to a 150-180 cadence.",),
            aliases=("corrida", "running", "run", "cardio", "esteira", "treadmill", "bike", "spinning"),
        ),
        Profile(
            key="study",
            label="Study / deep focus",
            description="Mostly instrumental, constant low-medium energy, no abrupt peaks.",
            acts=(
                Act("Settle in", 0.15, 3, 3.5, "calm, welcoming, low distraction"),
                Act("Deep focus", 0.70, 3.5, 3.5, "steady, repetitive, instrumental"),
                Act("Wind-down", 0.15, 3, 2, "softer, spacious"),
            ),
            bpm_range=(60, 110),
            avg_track_minutes=4.5,
            vocals="instrumental (no lyrics in the deep focus act)",
            max_bpm_step=10,
            max_energy_step=1.0,
            min_artist_gap=2,
            guidance=("Soundtracks, ambient, neoclassical, minimal, lo-fi; avoid drops and loud peaks.",),
            aliases=(
                "estudo",
                "estudar",
                "study",
                "focus",
                "foco",
                "concentracao",
                "trabalho",
                "work",
                "coding",
            ),
        ),
        Profile(
            key="party",
            label="Party / pre-game",
            description="Warm-up, rising, peak of hits and a sing-along finish.",
            acts=(
                Act("Arrival", 0.15, 5, 7, "feel-good, recognisable"),
                Act("Rising", 0.35, 7, 8.5, "danceable hits"),
                Act("Peak", 0.35, 9, 9.5, "biggest hits, maximum dancefloor"),
                Act("Sing-along finish", 0.15, 8, 6, "anthems everyone knows"),
            ),
            bpm_range=(100, 130),
            avg_track_minutes=3.5,
            vocals="prominent",
            max_bpm_step=10,
            max_energy_step=2.5,
            min_artist_gap=3,
            guidance=("Familiarity matters more than discovery here.",),
            aliases=("festa", "party", "curticao", "curtir", "churrasco", "esquenta", "pre", "aniversario"),
        ),
        Profile(
            key="chill",
            label="Chill / daily listening",
            description="A gentle wave: relaxed start, a mild lift in the middle, relaxed end.",
            acts=(
                Act("Easy start", 0.30, 4, 5, "relaxed"),
                Act("Gentle lift", 0.40, 5, 6, "a little more groove"),
                Act("Easy end", 0.30, 6, 4, "back to calm"),
            ),
            bpm_range=(80, 120),
            avg_track_minutes=3.8,
            vocals="any",
            max_bpm_step=10,
            max_energy_step=1.5,
            min_artist_gap=2,
            aliases=(
                "chill",
                "dia a dia",
                "daily",
                "relax",
                "tranquilo",
                "cafe",
                "domingo",
                "viagem",
                "road trip",
            ),
        ),
        Profile(
            key="sleep",
            label="Relax / sleep",
            description="Descending energy towards silence.",
            acts=(
                Act("Unwind", 0.40, 3, 2, "soft, warm"),
                Act("Drift", 0.60, 2, 1, "ambient, minimal, no percussion"),
            ),
            bpm_range=(50, 80),
            avg_track_minutes=5.0,
            vocals="none or very soft",
            max_bpm_step=10,
            max_energy_step=1.0,
            min_artist_gap=1,
            aliases=("dormir", "sono", "sleep", "meditacao", "meditation", "yoga", "relaxar"),
        ),
        Profile(
            key="exploration",
            label="Genre exploration",
            description="Five-act journey mixing eras and sub-genres of one genre.",
            acts=(
                Act("Opening", 0.15, 4, 5, "inviting entry points"),
                Act("Groove", 0.20, 5, 6, "the genre's core sound"),
                Act("Build", 0.25, 6, 7.5, "deeper cuts and sub-genres"),
                Act("Peak", 0.25, 7.5, 9, "landmark tracks"),
                Act("Cooldown", 0.15, 7, 4, "reflective closers"),
            ),
            bpm_range=(80, 140),
            avg_track_minutes=4.2,
            vocals="any",
            max_bpm_step=12,
            max_energy_step=2.5,
            min_artist_gap=3,
            guidance=("Mix eras and sub-genres; 1-2 tracks per discovery artist.",),
            aliases=(
                "explorar",
                "exploration",
                "descobrir",
                "discovery",
                "genero",
                "genre",
                "rock",
                "jazz",
                "mpb",
            ),
        ),
    )
}

DEFAULT_PROFILE = "exploration"


def get_profile(purpose: str | None) -> Profile:
    """Resolve a profile from a key or free text in English or Portuguese ("treino", "set de techno")."""
    if not purpose:
        return PROFILES[DEFAULT_PROFILE]
    text = normalize(purpose).replace(" ", "_")
    if text in PROFILES:
        return PROFILES[text]
    words = f" {normalize(purpose)} "
    best: tuple[int, Profile] | None = None
    for profile in PROFILES.values():
        for alias in profile.aliases:
            if f" {normalize(alias)} " in words and (best is None or len(alias) > best[0]):
                best = (len(alias), profile)
    return best[1] if best else PROFILES[DEFAULT_PROFILE]
