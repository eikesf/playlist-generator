"""Use-case operations shared by MCP tools and the CLI (application layer, DRY)."""

from __future__ import annotations

from typing import Any

from .bpm.service import align_octave
from .container import AppContainer
from .curation.profiles import get_profile
from .curation.sequencer import TrackFeatures, insert_tracks


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


async def insert_into_playlist(
    app: AppContainer,
    playlist_id: str,
    purpose: str,
    new_tracks: list[dict[str, Any]],
    current_estimates: list[dict[str, Any]] | None = None,
    allow_rebuild: bool = False,
) -> dict[str, Any]:
    """Insert tracks at their best positions keeping the current order (see ADR 0005)."""
    entries = await app.library.playlist_tracks(playlist_id)
    estimates = {str(t["id"]): t for t in current_estimates or []}
    new_ids = {str(t["id"]) for t in new_tracks}
    base: list[TrackFeatures] = []
    already = []
    for entry in entries:
        ref = entry.reference[0]
        if ref in new_ids:  # e.g. appended earlier: take it out and place it properly
            already.append(ref)
            continue
        given = estimates.get(ref) or estimates.get(entry.library_id)
        f = (
            TrackFeatures.from_dict({**given, "id": ref})
            if given
            else TrackFeatures(id=ref, artist=entry.artist)
        )
        f.artist = f.artist or entry.artist
        f.title = f.title or entry.name
        f.duration_ms = f.duration_ms or entry.duration_ms
        base.append(f)
    new = [TrackFeatures.from_dict(t) for t in new_tracks]
    await enrich_features(app, base + new)
    plan = insert_tracks(get_profile(purpose), base, new)
    unestimated = sum(1 for f in base if f.energy is None)
    if unestimated:
        plan["warnings"].append(
            f"{unestimated} existing track(s) had no bpm/energy estimate; placement relied on the others."
        )
    if already:
        plan["warnings"].append(f"Already in the playlist, moved to their best position: {already}")
    if not plan["inserted"]:
        return {"changed": False, "inserted": [], "warnings": plan["warnings"]}
    result = await app.editor.sync(playlist_id, plan["song_ids_in_order"], allow_rebuild)
    return {
        "edit": result.to_dict(),
        "inserted": plan["inserted"],
        "existing_order_preserved": True,
        "warnings": plan["warnings"],
    }
