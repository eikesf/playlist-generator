"""Order tracks so the playlist follows its purpose's energy curve.

Claude supplies an estimated BPM and energy (1-10) per track; the catalog has no tempo data.
The sequencer is deterministic:

1. Greedy pass: walk the timeline; at each slot pick the remaining track with the lowest cost
   against the target energy at that moment, the BPM of the previous track and artist spacing.
2. Local search: pairwise swaps that lower the total cost, until no swap helps (bounded passes).

Cost terms (weights below): distance to the target energy, BPM jumps beyond the profile's
``max_bpm_step`` (half/double time counts as compatible), energy jumps beyond ``max_energy_step``,
the same artist within ``min_artist_gap`` tracks, and placing a track outside a requested act.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import format_duration
from ..text import primary_artist
from .profiles import Profile

W_ENERGY = 3.0
W_BPM = 0.6
W_ENERGY_JUMP = 4.0
W_ARTIST = 25.0
W_ACT = 100.0  # a requested act is close to a hard constraint
DEFAULT_DURATION_MS = 300_000
MAX_SWAP_PASSES = 8


@dataclass
class TrackFeatures:
    id: str
    artist: str
    title: str = ""
    bpm: float | None = None
    energy: float | None = None
    duration_ms: int | None = None
    act: str | None = None  # optional: force the track into a named act
    bpm_source: str | None = None  # "estimate" (curator) or a database name ("deezer", "getsongbpm")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackFeatures:
        return cls(
            id=str(data["id"]),
            artist=str(data.get("artist") or ""),
            title=str(data.get("title") or data.get("name") or ""),
            bpm=_num(data.get("bpm")),
            energy=_num(data.get("energy")),
            duration_ms=int(data["duration_ms"]) if data.get("duration_ms") else None,
            act=(data.get("act") or None),
            bpm_source=data.get("bpm_source") or ("estimate" if _num(data.get("bpm")) else None),
        )

    @property
    def length(self) -> int:
        return self.duration_ms or DEFAULT_DURATION_MS


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def bpm_distance(a: float, b: float) -> float:
    """Tempo distance that treats half/double time as compatible (87 ~ 174)."""
    return min(abs(a - b), abs(2 * a - b), abs(a - 2 * b))


class Sequencer:
    def __init__(self, profile: Profile):
        self.profile = profile
        self._act_names = [a.name.lower() for a in profile.acts]

    # ------------------------------------------------------------------ cost
    def _slot_targets(self, order: list[TrackFeatures]) -> list[tuple[float, int]]:
        """(target energy, act index) at the middle of each track along the timeline."""
        total = sum(t.length for t in order) or 1
        elapsed = 0
        targets = []
        for t in order:
            mid = (elapsed + t.length / 2) / total
            idx, act, inner = self.profile.act_at(mid)
            targets.append((act.energy_at(inner), idx))
            elapsed += t.length
        return targets

    def _pair_cost(self, prev: TrackFeatures | None, cur: TrackFeatures) -> float:
        if prev is None:
            return 0.0
        cost = 0.0
        if prev.bpm and cur.bpm:
            excess = bpm_distance(prev.bpm, cur.bpm) - self.profile.max_bpm_step
            if excess > 0:
                cost += W_BPM * excess**1.5
        if prev.energy is not None and cur.energy is not None:
            excess = abs(cur.energy - prev.energy) - self.profile.max_energy_step
            if excess > 0:
                cost += W_ENERGY_JUMP * excess
        return cost

    def _slot_cost(self, track: TrackFeatures, target_energy: float, act_idx: int) -> float:
        cost = 0.0
        if track.energy is not None:
            cost += W_ENERGY * abs(track.energy - target_energy)
        if track.act:
            wanted = self._act_index(track.act)
            if wanted is not None and wanted != act_idx:
                cost += W_ACT * abs(wanted - act_idx)
        return cost

    def _artist_cost(self, order: list[TrackFeatures]) -> float:
        gap = self.profile.min_artist_gap
        cost = 0.0
        names = [primary_artist(t.artist) for t in order]
        for i, name in enumerate(names):
            for j in range(max(0, i - gap), i):
                if name and names[j] == name:
                    cost += W_ARTIST / (i - j)
        return cost

    def total_cost(self, order: list[TrackFeatures]) -> float:
        targets = self._slot_targets(order)
        cost = sum(self._slot_cost(t, e, a) for t, (e, a) in zip(order, targets, strict=True))
        cost += sum(self._pair_cost(order[i - 1] if i else None, t) for i, t in enumerate(order))
        return cost + self._artist_cost(order)

    def _act_index(self, name: str) -> int | None:
        key = name.lower().strip()
        for i, act_name in enumerate(self._act_names):
            if key == act_name or key in act_name:
                return i
        return None

    # ------------------------------------------------------------- algorithm
    def _greedy(self, tracks: list[TrackFeatures]) -> list[TrackFeatures]:
        remaining = sorted(tracks, key=lambda t: (t.energy if t.energy is not None else 5, t.bpm or 0, t.id))
        total = sum(t.length for t in tracks) or 1
        order: list[TrackFeatures] = []
        elapsed = 0
        while remaining:
            prev = order[-1] if order else None
            recent = {primary_artist(t.artist) for t in order[-self.profile.min_artist_gap :]}

            def cost(
                t: TrackFeatures,
                prev: TrackFeatures | None = prev,
                recent: set[str] = recent,
                at: int = elapsed,
            ) -> float:
                idx, act, inner = self.profile.act_at((at + t.length / 2) / total)
                c = self._slot_cost(t, act.energy_at(inner), idx) + self._pair_cost(prev, t)
                return c + (W_ARTIST if primary_artist(t.artist) in recent else 0.0)

            best = min(remaining, key=cost)
            remaining.remove(best)
            order.append(best)
            elapsed += best.length
        return order

    def _local_cost(
        self, order: list[TrackFeatures], names: list[str], targets: list[tuple[float, int]], idx: set[int]
    ) -> float:
        """Cost terms that involve the given positions only (for O(gap) swap deltas)."""
        n, gap = len(order), self.profile.min_artist_gap
        cost = sum(self._slot_cost(order[k], *targets[k]) for k in idx)
        edges = {k for i in idx for k in (i, i + 1) if 0 < k < n}
        cost += sum(self._pair_cost(order[k - 1], order[k]) for k in edges)
        pairs = {
            (min(i, k), max(i, k)) for i in idx for k in range(i - gap, i + gap + 1) if 0 <= k < n and k != i
        }
        cost += sum(W_ARTIST / (q - p) for p, q in pairs if names[p] and names[p] == names[q])
        return cost

    def _improve(self, order: list[TrackFeatures]) -> list[TrackFeatures]:
        """Pairwise swaps with incremental cost; slot targets are refreshed after each pass."""
        best_total = self.total_cost(order)
        for _ in range(MAX_SWAP_PASSES):
            snapshot = list(order)
            targets = self._slot_targets(order)
            names = [primary_artist(t.artist) for t in order]
            improved = False
            for i in range(len(order) - 1):
                for j in range(i + 1, len(order)):
                    before = self._local_cost(order, names, targets, {i, j})
                    order[i], order[j] = order[j], order[i]
                    names[i], names[j] = names[j], names[i]
                    if self._local_cost(order, names, targets, {i, j}) + 1e-9 < before:
                        improved = True
                    else:
                        order[i], order[j] = order[j], order[i]
                        names[i], names[j] = names[j], names[i]
            total = self.total_cost(order)
            if not improved or total + 1e-9 >= best_total:
                if total > best_total:
                    order[:] = snapshot
                break
            best_total = total
        return order

    def order(self, tracks: list[TrackFeatures]) -> list[TrackFeatures]:
        unique = list({t.id: t for t in tracks}.values())
        if len(unique) <= 2:
            return sorted(unique, key=lambda t: t.energy or 0)
        return self._improve(self._greedy(unique))

    def insert(
        self, base: list[TrackFeatures], new: list[TrackFeatures]
    ) -> tuple[list[TrackFeatures], list[dict[str, Any]]]:
        """Place each new track at its best slot while keeping ``base`` in its exact order.

        Repeatedly picks the (new track, slot) pair with the smallest cost increase, so the
        track that fits most clearly is placed first and later choices see it.
        """
        order = list(base)
        existing = {t.id for t in order}
        pending = list({t.id: t for t in new if t.id not in existing}.values())
        placed_ids: set[str] = set()
        while pending:
            current = self.total_cost(order)
            best: tuple[float, int, int] | None = None  # (delta, slot, pending index)
            for k, track in enumerate(pending):
                for slot in range(len(order) + 1):
                    candidate = order[:slot] + [track] + order[slot:]
                    delta = self.total_cost(candidate) - current
                    if best is None or delta < best[0] - 1e-9:
                        best = (delta, slot, k)
            assert best is not None
            _, slot, k = best
            order.insert(slot, pending.pop(k))
            placed_ids.add(order[slot].id)
        targets = self._slot_targets(order)
        placements = []
        for i, t in enumerate(order):
            if t.id in placed_ids:
                placements.append(
                    {
                        "id": t.id,
                        "track": f"{t.artist} - {t.title}".strip(" -"),
                        "position": i + 1,
                        "act": self.profile.acts[targets[i][1]].name,
                        "after": _label(order[i - 1]) if i else None,
                        "before": _label(order[i + 1]) if i + 1 < len(order) else None,
                    }
                )
        return order, placements

    # ---------------------------------------------------------------- report
    def report(self, order: list[TrackFeatures]) -> dict[str, Any]:
        targets = self._slot_targets(order)
        rows, warnings = [], []
        acts: dict[str, dict[str, Any]] = {}
        names = [primary_artist(t.artist) for t in order]
        for i, (t, (target, act_idx)) in enumerate(zip(order, targets, strict=True)):
            act_name = self.profile.acts[act_idx].name
            prev = order[i - 1] if i else None
            step = round(bpm_distance(prev.bpm, t.bpm), 1) if prev and prev.bpm and t.bpm else None
            rows.append(
                {
                    "position": i + 1,
                    "id": t.id,
                    "track": f"{t.artist} - {t.title}".strip(" -"),
                    "act": act_name,
                    "target_energy": round(target, 1),
                    "energy": t.energy,
                    "bpm": round(t.bpm, 1) if t.bpm else t.bpm,
                    "bpm_source": t.bpm_source,
                    "bpm_step": step,
                }
            )
            bucket = acts.setdefault(act_name, {"act": act_name, "tracks": 0, "duration_ms": 0})
            bucket["tracks"] += 1
            bucket["duration_ms"] += t.length
            if step is not None and step > self.profile.max_bpm_step * 2:
                warnings.append(f"#{i + 1}: BPM jump of {step} from the previous track")
            gap = self.profile.min_artist_gap
            if names[i] and names[i] in names[max(0, i - gap) : i]:
                warnings.append(
                    f"#{i + 1}: {t.artist} repeats within {gap} tracks (not enough other artists)"
                )
        for bucket in acts.values():
            bucket["duration"] = format_duration(bucket.pop("duration_ms"))
        missing = [t.id for t in order if t.energy is None or t.bpm is None]
        if missing:
            warnings.append(f"{len(missing)} track(s) had no BPM/energy estimate; placed by the others only")
        return {
            "purpose": self.profile.key,
            "song_ids_in_order": [t.id for t in order],
            "total_duration": format_duration(sum(t.length for t in order)),
            "acts": list(acts.values()),
            "tracks": rows,
            "warnings": warnings,
            "measured_bpm": sum(1 for t in order if t.bpm_source not in (None, "estimate")),
            "note": "Energy is the curator's estimate; BPM is measured where bpm_source names a database.",
        }


def _label(t: TrackFeatures) -> str:
    return f"{t.artist} - {t.title}".strip(" -") or t.id


def insert_tracks(profile: Profile, base: list[TrackFeatures], new: list[TrackFeatures]) -> dict[str, Any]:
    """Keep ``base`` order, slot ``new`` tracks where they fit the curve best."""
    sequencer = Sequencer(profile)
    order, placements = sequencer.insert(base, new)
    report = sequencer.report(order)
    report["inserted"] = placements
    skipped = sorted({t.id for t in new} & {t.id for t in base})
    if skipped:
        report["warnings"].append(f"Already in the playlist, not added again: {skipped}")
    return report


def order_tracks(profile: Profile, tracks: list[TrackFeatures]) -> dict[str, Any]:
    sequencer = Sequencer(profile)
    return sequencer.report(sequencer.order(tracks))
