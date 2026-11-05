# 0011. Familiarity mix: known songs and known artists are separate targets

**Status:** Accepted

## Context
The goal is a playlist with a high chance of being enjoyed, because it is grounded in the owner's real taste, that still brings discoveries. A single "known vs new" ratio hides an important case: the owner may know an artist but not most of their songs. Those deep cuts carry low risk and still feel new.

## Decision
- Ask **two separate targets** at creation: `known_tracks_pct` (songs already known) and `known_artists_pct` (tracks by artists already known, including known songs, so it is ≥ the first).
- Classify every track into one of three buckets using the taste profile (ADR 0003, PR #27). A song in the library or recently played is a `known_track`. A song whose credited artists include one the owner listens to (library, recent or heavy rotation) is a `known_artist_new_track`. Anything else is `new_artist`.
- `plan_playlist` turns the targets into bucket counts. `resolve_tracks` reports the real counts and a `mix_check` with deltas and concrete swap suggestions. The tolerance is 5% of the playlist, so 0 tracks for small playlists and 4 for 75 tracks.
- Each bucket has its own source:
  - `known_tracks`: library and recent plays, loved songs first.
  - `deep_cuts`: popular catalog songs by known artists that the owner hasn't heard.
  - `discover_tracks`: similar artists the owner doesn't know.

## Consequences
- The mix is measured, not guessed, and can be tuned independently for songs and for artists.
- Classification is only as good as the taste data. A song heard on another service, or an artist followed only elsewhere, counts as new.
