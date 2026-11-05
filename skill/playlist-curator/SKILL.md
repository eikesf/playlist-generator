---
name: "playlist-curator"
description: "Curates a themed playlist (genre, DJ-style set, workout, study, mood), verifies every track in the Apple Music catalog, orders it as a journey and creates it directly in Eike's Apple Music library through the apple-music MCP server. Use whenever Eike asks for a playlist, a set, a mix or music for an activity."
---

# Playlist Curator (Apple Music)

Eike listens on Apple Music (Mac + iPhone). The `apple-music` MCP server creates playlists directly in his library, so there is no Spotify and no Tune My Music in the normal flow. Reply in Portuguese (pt-BR).

Tools (server `apple-music`): `auth_status`, `artist_top_songs`, `search_songs`, `resolve_tracks`, `create_playlist`, `add_tracks_to_playlist`, `list_playlists`, `get_playlist_tracks`, `export_playlist_text`.

## 0. Preflight

Call `auth_status` once at the start.
- `ready_to_create_playlists: true`: continue.
- Otherwise tell Eike to run `apple-music-mcp login` in the Mac Terminal (the token expires from time to time), and keep curating. At the end, use the fallback in step 6.
- If the `apple-music` tools are missing entirely, the server is not connected. Point him to the README of github.com/eikesf/playlist-generator (`apple-music-mcp install-claude`).

## 1. Discovery (ask before searching)

Ask up to 4 questions in one round (use AskUserQuestion when available), picking the ones that matter:
- **Size**: total duration (e.g. 1h, 2h, 5h) or number of tracks. This is mandatory unless he already said it.
- **Energy curve**: journey/DJ set (atmospheric intro, build, peak, cooldown), constant/hypnotic, or high intensity throughout.
- **Known vs new ratio**: 40/60, 50/50, 20/80.
- **Vocals**: ethereal/sparse, mostly instrumental, prominent.
- Sub-genres to include or avoid.
- **Use case**: training, study/focus, daily listening, party, a specific mood.

Also ask for reference artists and one reference track that captures the vibe. If an artist name looks like a speech-to-text mangling, confirm the spelling (or check with `search_songs`) before going further.

## 2. Size and balance

- Convert duration to tracks with the genre's typical length: techno/house/trance about 6–7 min, melodic techno 7, pop/rock 3.5–4, hip-hop 3, ambient 6+. Example: 2h of techno ≈ 18–20 tracks.
- No size given: default about 75 tracks (≈5h). Never under 50 unless he asked for a short duration.
- Each core artist he named gets 3 to 5 tracks so he can judge their style. One track per artist is not enough.
- Discovery artists: 3+ tracks for the 4–5 most promising, 1–2 for the rest.
- Respect the known/new ratio.
- Aim for the target duration ±5% using the `total_duration` returned by `resolve_tracks`. Add or remove tracks until it fits.

## 3. Gather and verify (never from memory alone)

1. For each core artist, call `artist_top_songs` (limit 10) to see what exists and what is popular.
2. Draft the full list from your knowledge plus those results, one line per track: `Artist1, Artist2 - Title (Remix)`.
3. Call `resolve_tracks` with the whole list (batches of up to 40 lines). Rules:
   - Use only `matched` tracks. Look at `low_confidence` ones (candidates are listed) and either pick a candidate with `search_songs` or drop it.
   - `not_found`: replace with another track by the same artist (check `artist_top_songs`) or drop it. Never invent ids.
   - Duplicates (radio edit vs original) are removed automatically. Keep the original or extended mix for DJ-style sets, the radio edit for workouts.
4. Resolve replacements again until the list is clean.

## 4. Order the journey

The catalog has no BPM or energy data, so estimate each track's BPM and energy (1–10) from your knowledge of the track, artist and sub-genre. Order by that plan. Keep the estimates internal and say they are estimates if he asks.

Default five-act structure (adjust per use case):
1. Atmospheric opening (low energy, slower, melodic or deep)
2. Groove / warm-up
3. Melodic build
4. Peak (hardest, highest BPM, biggest tracks)
5. Cooldown

DJ-set rules (techno, house, trance): move BPM in small steps (about ±3 BPM between neighbours), never drop energy by more than 2 points mid-set, avoid two tracks by the same artist back to back, put the most recognisable tracks in the peak, end on a melodic or emotional track.

Use-case templates:
- **Workout**: short warm-up (10%), long sustained high-energy middle (80%), short cooldown. Prefer tracks under 6 min.
- **Study/focus**: mostly instrumental, constant energy, no abrupt peaks (soundtracks, ambient, neoclassical, minimal).
- **Genre exploration** (rock, electronic, etc.): five-act journey mixing eras and sub-genres.

## 5. Create in Apple Music

1. Decide the name (decisive, evocative, max ~40 chars) and a description (≤ 250 chars).
2. Call `create_playlist` with `name`, `description` and `song_ids` = the ordered ids (from `song_ids_in_order` after you reordered them). Do not reorder after creating.
3. Optionally confirm with `get_playlist_tracks` that the count matches.
4. Reply with:
   - The playlist link (`playlist.url`) and that it shows up on his iPhone within a few minutes (iCloud Music Library sync).
   - Tracks grouped by act, with total duration per act. On mobile or for long lists, show a summary per act and the highlights only.
   - Why the name was chosen, plus two alternative names and two descriptions ready to paste (one long, one short). He renames it in the Music app if he prefers an alternative.
   - Honest notes: artists that ended with too few tracks and why (not in the catalog, region-locked, only remixes, etc.), and tracks that were dropped.

## 6. Fallback (library unreachable)

If `create_playlist` fails with `AUTH` and he cannot log in right now, call `export_playlist_text` with the ordered lines (`Artist - Title`). Give him the file path and the import steps: tunemymusic.com, source "Free text" or "Upload file", destination Apple Music, then check the "Missing" report.

## 7. Follow-up

- To extend a playlist, use `list_playlists` to find it, `resolve_tracks` for the new tracks and `add_tracks_to_playlist` (tracks are appended at the end).
- Offer a deeper second playlist focused on the artists he liked most after a week or two of listening.
