# 0003. Apple Music catalog as the only source of truth

**Status:** Accepted

## Context
Verifying tracks on Spotify and then matching them on Apple Music adds a second matching step that can fail: regional availability, different titles, remasters. The destination is Apple Music.

## Decision
- Search and verify directly in the Apple Music catalog (`/v1/catalog/{storefront}/search`) for the user's storefront (for example `br`).
- `resolve_tracks` maps free-text lines (`Artist1, Artist2 - Title (Remix)`) to catalog ids. It uses fuzzy title/artist scoring (rapidfuzz) and penalizes versions nobody asked for (live, karaoke, covers, a different remixer). It removes duplicates (radio edit vs original) and reports `not_found` instead of guessing.
- If amp-api is unavailable, fall back to the public iTunes Search API, whose `trackId` is the same catalog id and needs no auth.

## Consequences
- What Claude verifies is exactly what gets added. There is one matching step instead of two.
- Spotify is no longer needed at all.
- The score thresholds (match ≥ 80, low confidence ≥ 65) are heuristics that the tests tune. Live usage may suggest adjustments.
