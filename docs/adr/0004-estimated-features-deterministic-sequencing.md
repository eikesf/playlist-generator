# 0004. The LLM estimates BPM/energy; a deterministic algorithm sequences

**Status:** Accepted

## Context
A good playlist follows its purpose. A DJ-style techno set builds from an atmospheric opening to a peak and a cooldown. A workout needs a long high plateau, and study music needs constant low energy. Ordering needs tempo and energy per track, but the Apple Music catalog exposes neither, and Spotify closed its audio-features endpoint to new apps in late 2024. Asking the LLM to order 75 tracks directly is neither reproducible nor checkable.

## Decision
- Model each purpose as data: a **profile** with acts (share of duration, energy start→end), a BPM range, track length, vocals and transition rules (`curation/profiles.py`). There are 8 profiles: `dj_set`, `workout`, `running`, `study`, `party`, `chill`, `sleep`, `exploration`. Free text in pt-BR or English selects one.
- `plan_playlist` turns purpose + duration into acts, with track counts and target energy and BPM.
- Claude estimates `bpm` and `energy` (1–10) per verified track. `order_tracks` sequences the tracks deterministically. It runs a greedy pass along the timeline, then a pairwise-swap local search with incremental cost.
- The cost terms are: distance to the curve's target energy, BPM jumps beyond the profile step (half/double time counts as compatible), energy jumps, the same artist within N tracks, and pinned acts.

## Consequences
- The same input always gives the same order. The behavior is unit-tested: a calm opening, a peak in the second half, a cooldown ending, no back-to-back artist, and 100 tracks in under a second.
- The quality of the order depends on the quality of the estimates, which are labelled as estimates everywhere.
- A new purpose is a new profile entry. The algorithm stays untouched (open/closed).
