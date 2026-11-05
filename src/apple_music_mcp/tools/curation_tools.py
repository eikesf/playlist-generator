"""Curation tools: purpose profiles, playlist planning and set sequencing."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ..bpm.service import align_octave
from ..container import AppContainer
from ..curation.planner import plan_playlist as make_plan
from ..curation.profiles import PROFILES, get_profile
from ..curation.sequencer import TrackFeatures
from ..curation.sequencer import order_tracks as sequence
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


async def enrich_features(
    app: AppContainer, features: list[TrackFeatures], use_measured_bpm: bool = True
) -> None:
    """Fill durations from the catalog and replace estimated BPM with measured BPM when found."""
    await fill_durations(app, features)
    if not use_measured_bpm or not app.bpm.providers:
        return
    lookups = [{"id": f.id, "artist": f.artist, "title": f.title} for f in features if f.artist and f.title]
    measured = await app.bpm.lookup_many(lookups)
    for f in features:
        match = measured.get(f.id)
        if match:
            f.bpm = align_octave(match.bpm, f.bpm)
            f.bpm_source = match.source


async def fill_durations(app: AppContainer, features: list[TrackFeatures]) -> None:
    """Fetch missing track durations from the catalog (best effort; timing only)."""
    missing = [f.id for f in features if not f.duration_ms and not f.id.startswith("i.")]
    if not missing:
        return
    try:
        durations = {s.id: s.duration_ms for s in await app.catalog.get_songs(missing)}
    except Exception:  # noqa: BLE001 - durations are a nice-to-have
        return
    for f in features:
        f.duration_ms = f.duration_ms or durations.get(f.id)


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
    async def list_playlist_profiles() -> dict[str, Any]:
        """List the purpose profiles (study, workout, running, party, dj_set, chill, sleep, exploration)
        with their energy curves, tempo ranges and rules. Use it to pick the purpose that fits the request."""
        return {
            "profiles": [
                {"key": p.key, "label": p.label, "description": p.description, "aliases": list(p.aliases)}
                for p in PROFILES.values()
            ]
        }

    @mcp.tool()
    async def plan_playlist(
        purpose: str, duration_minutes: float | None = None, track_count: int | None = None
    ) -> dict[str, Any]:
        """Build the plan for a playlist from its purpose (key or free text like "treino", "set de techno",
        "estudo") and its size (minutes or tracks). Returns acts with minutes, number of tracks, target
        energy and BPM per act, vocals and transition rules. Curate against this plan."""
        return make_plan(get_profile(purpose), duration_minutes, track_count).to_dict()

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
        return sequence(get_profile(purpose), features)
