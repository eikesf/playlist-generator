"""Purpose profiles. The purpose of a playlist decides its energy curve, tempo, track length and vocals.

Energy is on a 1-10 scale. Each act covers a share of the total duration and interpolates its
energy linearly from ``energy_start`` to ``energy_end``. Profiles are data: add one here and every
tool (plan, order, skill guidance) picks it up (open/closed principle).

Profiles come in families (owner's decision): a family profile (``dj_set``, ``workout``, ``focus``...)
and sub-profiles that refine it (``focus.binaural``, ``workout.hiit``...). A sub-profile inherits
everything from its family and overrides what differs. Claude can also create profiles in a
conversation; those live in the database (``curation/registry.py``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
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
        if fraction >= 1 - 1e-9:  # absorb float error from summing act shares
            return self.energy_end
        fraction = max(0.0, fraction)
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
    family: str = ""  # family key; empty means "this profile is a family"
    origin: str = "builtin"  # builtin | ai_created | owner

    @property
    def family_key(self) -> str:
        return self.family or self.key

    @property
    def is_family(self) -> bool:
        return not self.family

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
        data["family"] = self.family_key
        return data

    def curve(self, points: int = 21) -> list[dict[str, float]]:
        """Sampled energy and target BPM from start (0) to end (1), for showing and validating a profile."""
        points = max(2, points)
        out = []
        for i in range(points):
            t = i / (points - 1)
            energy = self.energy_at(t)
            out.append(
                {"t": round(t, 3), "energy": round(energy, 2), "bpm": round(self.bpm_for_energy(energy))}
            )
        return out


def variant(base: Profile, key: str, label: str, description: str, **changes: Any) -> Profile:
    """A sub-profile of ``base``: same rules unless overridden. Aliases are never inherited."""
    changes.setdefault("aliases", ())
    return replace(base, key=key, label=label, description=description, family=base.family_key, **changes)


_FAMILIES: dict[str, Profile] = {
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
            key="focus",
            label="Focus / deep work",
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
            guidance=(
                "Soundtracks, ambient, neoclassical, minimal, lo-fi; avoid drops and loud peaks.",
                "Nothing heavy (owner's rule for focus): no metal, no hard techno, no aggressive vocals.",
            ),
            aliases=(
                "foco",
                "focus",
                "concentracao",
                "concentrar",
                "trabalho",
                "work",
                "deep work",
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


def _variants(f: dict[str, Profile]) -> tuple[Profile, ...]:
    return (
        variant(
            f["focus"],
            "study",
            "Study",
            "Reading and studying: calm instrumental, steady, low energy.",
            acts=(
                Act("Settle in", 0.15, 3, 3.5, "calm, welcoming, low distraction"),
                Act("Deep focus", 0.70, 3.5, 3.5, "steady, repetitive, instrumental"),
                Act("Wind-down", 0.15, 3, 2, "softer, spacious"),
            ),
            bpm_range=(60, 110),
            guidance=("Lo-fi, neoclassical, ambient piano, minimal; no lyrics in the deep focus act.",),
            aliases=("estudo", "estudar", "study", "studying", "leitura", "ler", "reading"),
        ),
        variant(
            f["focus"],
            "binaural",
            "Focus · binaural / brainwave",
            "Binaural beats and drones for concentration: almost flat, very low energy.",
            acts=(
                Act("Tune in", 0.10, 2, 2, "drones, slow pulses"),
                Act("Hold", 0.80, 2, 2, "constant binaural tones, no melody changes"),
                Act("Release", 0.10, 2, 1.5, "fade"),
            ),
            bpm_range=(40, 80),
            avg_track_minutes=10.0,
            vocals="none",
            max_energy_step=0.5,
            min_artist_gap=1,
            guidance=("Binaural beats need headphones. Prefer long tracks (10+ min) from the same series.",),
            aliases=("binaural", "binaurais", "ondas binaurais", "brainwave", "ondas alfa", "alpha waves"),
        ),
        variant(
            f["focus"],
            "epic_soundtrack",
            "Focus · epic soundtrack",
            "Film and game scores: a slow build to an inspiring middle, no lyrics.",
            acts=(
                Act("Overture", 0.20, 3, 4.5, "atmospheric scores"),
                Act("Momentum", 0.50, 4.5, 6.5, "driving orchestral themes, no vocals"),
                Act("Resolution", 0.30, 6, 4, "emotional, spacious"),
            ),
            bpm_range=(70, 130),
            avg_track_minutes=4.0,
            vocals="instrumental (choirs without words are fine)",
            max_energy_step=1.5,
            guidance=(
                "Hans Zimmer-style scores, game soundtracks, epic orchestral; no battle cues at full volume.",
            ),
            aliases=(
                "trilha",
                "trilha sonora",
                "trilhas sonoras",
                "soundtrack",
                "epic",
                "epica",
                "epico",
                "film score",
            ),
        ),
        variant(
            f["workout"],
            "hiit",
            "Workout · HIIT intervals",
            "Alternating peaks and short recoveries.",
            acts=(
                Act("Warm-up", 0.10, 5, 7, "getting the body moving"),
                Act("Interval 1", 0.20, 9, 9.5, "maximum effort"),
                Act("Recover 1", 0.10, 6, 6, "breathe"),
                Act("Interval 2", 0.20, 9, 10, "maximum effort"),
                Act("Recover 2", 0.10, 6, 6, "breathe"),
                Act("Interval 3", 0.20, 9.5, 10, "the hardest round"),
                Act("Cooldown", 0.10, 6, 4, "release"),
            ),
            max_energy_step=3.5,
            aliases=("hiit", "intervalado", "intervalos", "tabata", "circuito", "circuit"),
        ),
        variant(
            f["workout"],
            "strength",
            "Workout · strength / heavy lifting",
            "Heavy, steady drive with room between sets.",
            acts=(
                Act("Warm-up", 0.10, 5, 7, "rhythmic"),
                Act("Working sets", 0.80, 7.5, 8.5, "heavy riffs and big drums, steady"),
                Act("Cooldown", 0.10, 6, 4, "release"),
            ),
            bpm_range=(100, 140),
            guidance=("Heavier genres fit (rock, metal, phonk, trap); groove matters more than speed.",),
            aliases=("forca", "musculacao pesada", "strength", "powerlifting", "heavy", "pesado", "maromba"),
        ),
        variant(
            f["dj_set"],
            "melodic_techno",
            "DJ set · melodic techno",
            "Emotional melodic techno and progressive: long builds, euphoric peak.",
            bpm_range=(118, 128),
            guidance=(
                "Prefer original/extended mixes; melodic leads and breakdowns; end on an emotional track.",
            ),
            aliases=("melodic techno", "techno melodico", "progressive", "afterlife", "melodic house"),
        ),
        variant(
            f["dj_set"],
            "peak_time_techno",
            "DJ set · peak-time / hard techno",
            "Driving, hard and fast: short opening, long peak.",
            acts=(
                Act("Opening", 0.10, 5, 6, "driving, hypnotic"),
                Act("Build", 0.25, 6.5, 8.5, "relentless, industrial"),
                Act("Peak", 0.50, 9, 10, "hardest and fastest"),
                Act("Exit", 0.15, 8.5, 6.5, "still driving, less intensity"),
            ),
            bpm_range=(128, 150),
            guidance=("Hard techno, industrial, schranz-leaning peaks; keep BPM steps small.",),
            aliases=("hard techno", "peak time", "industrial", "techno pesado", "rave pesada"),
        ),
    )


PROFILES: dict[str, Profile] = {**_FAMILIES, **{p.key: p for p in _variants(_FAMILIES)}}

DEFAULT_PROFILE = "exploration"


def get_profile(purpose: str | None) -> Profile:
    """Resolve a profile from a key or free text in English or Portuguese ("treino", "set de techno")."""
    if not purpose:
        return PROFILES[DEFAULT_PROFILE]
    return match_profile(purpose, PROFILES) or PROFILES[DEFAULT_PROFILE]


def match_profile(purpose: str, profiles: dict[str, Profile]) -> Profile | None:
    """Exact key (``binaural``, ``focus.binaural``) first, then the longest alias found in the text."""
    text = normalize(purpose).replace(" ", "_")
    key = text.split(".")[-1] if "." in purpose else text
    if key in profiles:
        return profiles[key]
    words = f" {normalize(purpose)} "
    hits: dict[str, int] = {}  # profile key -> longest matching alias
    for profile in profiles.values():
        for alias in (*profile.aliases, profile.label):
            if f" {normalize(alias)} " in words:
                hits[profile.key] = max(hits.get(profile.key, 0), len(alias))
    if not hits:
        return None
    # A sub-profile beats its own family ("treino hiit" means HIIT, not just workout); otherwise the
    # longest matching alias wins.
    refined = {k: n for k, n in hits.items() if not profiles[k].is_family and profiles[k].family_key in hits}
    pool = refined or hits
    return profiles[max(pool, key=lambda k: pool[k])]
