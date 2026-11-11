"""Curation tools: purpose profiles, playlist planning and set sequencing."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ..curation.familiarity import MixTargets, mix_counts
from ..curation.planner import plan_playlist as make_plan
from ..curation.sequencer import TrackFeatures
from ..curation.sequencer import order_tracks as sequence
from ..operations import enrich_features
from .base import ContainerProvider, tool_errors


class TrackInput(BaseModel):
    id: str = Field(description="Apple Music catalog song id (from resolve_tracks)")
    artist: str
    title: str = ""
    bpm: float | None = Field(default=None, description="Estimated BPM (your best estimate)")
    energy: float | None = Field(
        default=None, ge=1, le=10, description="Estimated energy, 1 (calm) to 10 (peak)"
    )
    duration_ms: int | None = Field(
        default=None, description="Optional; fetched from the catalog when missing"
    )
    act: str | None = Field(default=None, description="Optional: force into an act by name, e.g. 'Peak'")


def mix_targets(
    known_artists_pct: float | None,
    known_within_pct: float | None,
    known_tracks_pct: float | None = None,
) -> tuple[MixTargets, bool]:
    """Targets from the tool arguments, and whether the default mix (50 % / 30 %) was used."""
    if known_artists_pct is None and known_within_pct is None and known_tracks_pct is None:
        return MixTargets.default(), True
    default = MixTargets.default()
    ka = known_artists_pct if known_artists_pct is not None else default.known_artists_pct
    if known_within_pct is not None:
        return MixTargets.from_levels(ka, known_within_pct), False
    kt = known_tracks_pct if known_tracks_pct is not None else default.known_tracks_pct
    return MixTargets(kt, ka if known_artists_pct is not None else max(ka, kt)), False


class ActInput(BaseModel):
    name: str
    share: float = Field(gt=0, le=1, description="Fraction of the playlist; all acts add up to 1")
    energy_start: float = Field(ge=1, le=10)
    energy_end: float = Field(ge=1, le=10)
    intent: str = Field(description="What the act should feel like")


def register(mcp: MCPServer, provider: ContainerProvider) -> None:
    @mcp.tool()
    @tool_errors
    async def lookup_bpm(tracks: list[TrackInput]) -> dict[str, Any]:
        """Measured BPM for tracks (id, artist, title) from public databases (GetSongBPM if a key is
        configured, then Deezer). Tracks not found keep `bpm: null`: use your estimate for those."""
        app = provider.get()
        measured = await app.bpm.lookup_many(
            [{"id": t.id, "artist": t.artist, "title": t.title} for t in tracks]
        )
        rows = []
        for t in tracks:
            m = measured.get(t.id)
            rows.append(
                {
                    "id": t.id,
                    "track": f"{t.artist} - {t.title}",
                    "bpm": m.bpm if m else None,
                    "source": m.source if m else None,
                    "matched": m.matched if m else None,
                }
            )
        return {"providers": app.bpm.available, "found": sum(1 for r in rows if r["bpm"]), "tracks": rows}

    @mcp.tool()
    @tool_errors
    async def list_playlist_profiles() -> dict[str, Any]:
        """List the purpose profiles grouped in families (dj_set, workout, running, focus, party, chill,
        sleep, exploration) with their sub-profiles (e.g. focus → study, binaural, epic_soundtrack;
        workout → hiit, strength; dj_set → melodic_techno, peak_time_techno) and any profile created in
        earlier conversations. Pick the most specific profile that fits; if none fits, propose one with
        create_profile."""
        profiles = provider.get().profiles.all()
        families: dict[str, dict[str, Any]] = {}
        for p in profiles.values():
            if p.is_family:
                families[p.key] = {
                    "key": p.key,
                    "label": p.label,
                    "description": p.description,
                    "sub_profiles": [],
                }
        for p in profiles.values():
            if not p.is_family:
                families[p.family_key]["sub_profiles"].append(
                    {"key": p.key, "label": p.label, "description": p.description, "origin": p.origin}
                )
        return {"families": list(families.values())}

    @mcp.tool()
    @tool_errors
    async def profile_curve(purpose: str, points: int = 11) -> dict[str, Any]:
        """Energy (1-10) and target BPM from the start to the end of a profile, to show the user how a
        playlist will evolve before building it."""
        p = provider.get().profiles.get(purpose)
        return {
            "profile": p.key,
            "family": p.family_key,
            "acts": [
                {"name": a.name, "share": a.share, "energy": [a.energy_start, a.energy_end]} for a in p.acts
            ],
            "curve": p.curve(min(max(points, 2), 101)),
        }

    @mcp.tool()
    @tool_errors
    async def create_profile(
        key: str,
        label: str,
        description: str,
        family: str,
        acts: list[ActInput],
        bpm_min: int,
        bpm_max: int,
        avg_track_minutes: float = 4.0,
        vocals: str = "any",
        guidance: list[str] | None = None,
        aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new purpose profile when no existing one fits the user's request, after agreeing on it
        with the user. It joins a family (`family`: one of the family keys) and is stored for future
        sessions. `acts` must cover the whole playlist: their `share` values add up to 1. Keys are
        lowercase with underscores (e.g. `lofi_cooking`)."""
        from ..curation.profiles import Act, Profile

        profile = Profile(
            key=key.strip().lower().replace(" ", "_"),
            label=label,
            description=description,
            acts=tuple(Act(a.name, a.share, a.energy_start, a.energy_end, a.intent) for a in acts),
            bpm_range=(bpm_min, bpm_max),
            avg_track_minutes=avg_track_minutes,
            vocals=vocals,
            guidance=tuple(guidance or ()),
            aliases=tuple(aliases or ()),
            family=family,
            origin="ai_created",
        )
        if bpm_min >= bpm_max:
            raise ValueError("bpm_min must be lower than bpm_max")
        created = provider.get().profiles.create(profile)
        return {"created": created.key, "family": created.family_key, "curve": created.curve(11)}

    @mcp.tool()
    @tool_errors
    async def delete_profile(key: str) -> dict[str, Any]:
        """Delete a profile created in a conversation. Built-in profiles and profiles used by a managed
        playlist cannot be deleted."""
        return {"deleted": provider.get().profiles.delete(key), "key": key}

    @mcp.tool()
    @tool_errors
    async def plan_playlist(
        purpose: str,
        duration_minutes: float | None = None,
        track_count: int | None = None,
        known_artists_pct: float | None = None,
        known_tracks_within_known_artists_pct: float | None = None,
        known_tracks_pct: float | None = None,
    ) -> dict[str, Any]:
        """Build the plan for a playlist from its purpose (key or free text like "treino", "set de techno",
        "foco binaural") and its size (minutes or tracks; if the user skips it, use 300 minutes). Returns
        acts with minutes, number of tracks, target energy and BPM per act, vocals and transition rules.

        `familiarity_mix` gives how many tracks go in each bucket, from the user's two-level mix:
        level 1 `known_artists_pct` (share of the playlist by artists they know) and level 2
        `known_tracks_within_known_artists_pct` (share of songs they already know *among* those).
        Without targets the default is 50 % / 30 %. (`known_tracks_pct`, a share of the whole
        playlist, is still accepted instead of level 2.)"""
        plan = make_plan(provider.get().profiles.get(purpose), duration_minutes, track_count).to_dict()
        targets, is_default = mix_targets(
            known_artists_pct, known_tracks_within_known_artists_pct, known_tracks_pct
        )
        plan["familiarity_mix"] = {**mix_counts(plan["total_tracks"], targets), "default_mix": is_default}
        return plan

    @mcp.tool(name="order_tracks")
    @tool_errors
    async def order_tracks_tool(
        purpose: str, tracks: list[TrackInput], use_measured_bpm: bool = True
    ) -> dict[str, Any]:
        """Sequence verified tracks so the playlist follows the purpose's energy curve.

        Give every track your estimated `bpm` and `energy` (1-10). Returns `song_ids_in_order` (pass it
        to create_playlist / reorder_playlist), the act of each track, per-act durations and warnings
        (big BPM jumps, artist repeats). Deterministic: same input, same order. With `use_measured_bpm`
        (default) real BPM is looked up in public databases and replaces your estimate when found;
        each track reports `bpm_source`. Always pass `title` so the lookup can match."""
        features = [TrackFeatures.from_dict(t.model_dump()) for t in tracks]
        await enrich_features(provider.get(), features, use_measured_bpm)
        return sequence(provider.get().profiles.get(purpose), features)
