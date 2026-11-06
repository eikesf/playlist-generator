# ADR 0012 — What "known" means, and the two-level mix

**Status:** accepted (guide §7.4, owner's definitions) · **Supersedes:** the bucket rules of ADR 0011 (the three buckets stay)

## Context
ADR 0011 counted a track as known when it was in the library or recently played. The owner's guide is stricter. A song added to the library and played twice is not "known", and a song played 30 times on a radio station is.

## Decision
- **Known track**: loved (Apple Music rating or Music app favorite) or played `KNOWN_MIN_PLAYS` times or more (default 10, set in `.env`).
- **Partly known**: in the library or played 1–9 times, and not loved. It counts in neither group. Claude is told to swap it unless the owner wants it.
- **Never heard**: 0 plays and not in the library.
- **Known artist**: has at least one known track. An artist with only partly-known songs is still a new artist.
- **Two levels**: level 1 is the share of the playlist by known artists. Level 2 is the share of known songs *within* those artists. The default is 50 % / 30 %, which makes 15 % of the playlist known songs. Internally the targets stay as shares of the whole playlist, so `mix_counts` and `mix_check` are unchanged.
- Play counts come from the daily Music app snapshots (`play_snapshot`), matched to catalog tracks by normalized artist + title. Until the first snapshot exists, the old library/recent rules apply and the result says `familiarity_definitions: "fallback"`.

## Consequences
- The first `apple-music-mcp run snapshot` on the Mac turns the guide's definitions on.
- Radio and station plays count once the snapshots see them: the Music app counts every play of a library track. Plays of songs that are not in the library are only visible in recently played, so they stay partly known.
