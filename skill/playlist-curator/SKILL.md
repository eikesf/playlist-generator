---
name: "playlist-curator"
description: "Curates a playlist for a purpose (study, workout, running, party, DJ-style techno set, chill, sleep, genre exploration), verifies every track in the Apple Music catalog, sequences it along the purpose's energy curve and creates it directly in Eike's Apple Music library through the apple-music MCP server. Use whenever Eike asks for a playlist, a set, a mix or music for an activity."
---

# Playlist Curator (Apple Music)

Eike listens on Apple Music (Mac + iPhone). The `apple-music` MCP server creates playlists directly in his library, so there is no Spotify and no Tune My Music in the normal flow. Reply in Portuguese (pt-BR).

Tools (server `apple-music`): `auth_status`, `list_playlist_profiles`, `plan_playlist`, `artist_top_songs`, `search_songs`, `resolve_tracks`, `order_tracks`, `create_playlist`, `add_tracks_to_playlist`, `list_playlists`, `get_playlist_tracks`, `export_playlist_text`.

The one rule above all: **the purpose of the playlist decides everything.** A study playlist, a workout and a techno set differ in energy curve, tempo, track length and vocals. Find out the purpose first, then let the plan drive the curation.

## 0. Preflight

Call `auth_status` once at the start.
- `ready_to_create_playlists: true`: continue.
- Otherwise tell Eike to run `apple-music-mcp login` in the Mac Terminal (the token expires from time to time), and keep curating. At the end, use the fallback in step 7.
- If the `apple-music` tools are missing entirely, the server is not connected. Point him to the README of github.com/eikesf/playlist-generator (`apple-music-mcp install-claude`).

## 1. Understand the purpose (ask before searching)

Ask up to 4 questions in one round (use AskUserQuestion when available). Skip the ones he already answered:
- **Purpose / context**: study or deep focus, workout, running, party, DJ-style set (like his techno set), chill/daily, sleep, genre exploration. Ask what he will be doing while listening.
- **Size**: total duration (1h, 2h, 5h) or number of tracks. Mandatory.
- **Energy curve** when the purpose allows a choice: journey/DJ set (opening, build, peak, cooldown), constant/hypnotic, or high intensity throughout.
- **Known vs new ratio**: 40/60, 50/50, 20/80.
- **Vocals**: ethereal/sparse, mostly instrumental, prominent.
- Sub-genres to include or avoid.

Also ask for reference artists and one reference track that captures the vibe. If an artist name looks like a speech-to-text mangling, confirm the spelling (or check with `search_songs`) before going further.

## 2. Plan

Call `plan_playlist` with the purpose (free text works: "set de techno", "treino", "estudo") and `duration_minutes` or `track_count`. The plan returns:
- the acts with minutes, number of tracks, target energy (1–10) and target BPM per act,
- vocals, max track length, transition rules (max BPM step, max energy step, spacing between tracks by the same artist) and genre guidance.

If the purpose is unclear, call `list_playlist_profiles` and pick the closest one. Say which one you picked and why in one line. Fill each act with tracks that match its intent.

## 3. Size and balance

- No size given: the plan defaults to about 75 tracks. Never under 50 unless he asked for a short duration.
- Each core artist he named gets 3 to 5 tracks so he can judge their style. One track per artist is not enough.
- Discovery artists: 3+ tracks for the 4–5 most promising, 1–2 for the rest.
- Respect the known/new ratio.
- Draft about 15% more tracks than the plan needs. Some will not be in the catalog.

## 4. Gather and verify (never from memory alone)

1. For each core artist, call `artist_top_songs` (limit 10) to see what exists and what is popular.
2. Draft the full list from your knowledge plus those results, one line per track: `Artist1, Artist2 - Title (Remix)`, grouped by the act you intend.
3. Call `resolve_tracks` with the whole list (batches of up to 40 lines). Rules:
   - Use only `matched` tracks. Review `low_confidence` ones (candidates are listed) and either pick a candidate with `search_songs` or drop it.
   - `not_found`: replace it with another track by the same artist (check `artist_top_songs`) or drop it. Never invent ids.
   - Duplicates (radio edit vs original) are removed automatically. Keep the original or extended mix for DJ sets and the radio edit for workouts.
4. Resolve replacements until the list is clean and the total duration is within ±5% of the plan.

## 5. Sequence

The catalog has no BPM or energy data. For every verified track, estimate:
- `bpm`: your best estimate of the track's tempo,
- `energy`: 1 (calm, sparse) to 10 (peak, hardest, biggest drop), relative to the purpose,
- optionally `act` to pin a track into an act (e.g. the closing track into "Cooldown").

Call `order_tracks` with the purpose and the tracks (id, artist, title, bpm, energy). It returns `song_ids_in_order` following the plan's energy curve, with smooth BPM steps, no same artist back to back and pinned acts respected. Read its `warnings`. If it reports big BPM jumps or artist repeats, swap a track and call it again. Do not reorder by hand afterwards. Say the BPM and energy are estimates if he asks.

## 6. Create in Apple Music

1. Decide the name (decisive, evocative, max ~40 chars) and a description (≤ 250 chars).
2. Call `create_playlist` with `name`, `description` and `song_ids` = `song_ids_in_order` from `order_tracks`.
3. Optionally confirm with `get_playlist_tracks` that the count matches.
4. Reply with:
   - The playlist link (`playlist.url`) and a note that it shows up on his iPhone within a few minutes (iCloud Music Library sync).
   - Which purpose profile was used, in one line.
   - Tracks grouped by act, with the duration per act (from `order_tracks`). On mobile or for long lists, show a summary per act and the highlights only.
   - Why the name was chosen, plus two alternative names and two descriptions ready to paste (one long, one short). He renames it in the Music app if he prefers an alternative.
   - Honest notes: artists that ended with too few tracks and why (not in the catalog, region-locked, only remixes, etc.), and tracks that were dropped.

## 7. Fallback (library unreachable)

If `create_playlist` fails with `AUTH` and he cannot log in right now, call `export_playlist_text` with the ordered lines (`Artist - Title`). Give him the file path and the import steps: tunemymusic.com, source "Free text" or "Upload file", destination Apple Music, then check the "Missing" report.

## 8. Follow-up

- To extend a playlist, use `list_playlists` to find it, `resolve_tracks` for the new tracks and `add_tracks_to_playlist` (tracks are appended at the end).
- Offer a deeper second playlist focused on the artists he liked most after a week or two of listening.
