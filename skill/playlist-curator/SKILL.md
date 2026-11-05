---
name: "playlist-curator"
description: "Curates a playlist for a purpose (study, workout, running, party, DJ-style techno set, chill, sleep, genre exploration), verifies every track in the Apple Music catalog, sequences it along the purpose's energy curve and creates it directly in Eike's Apple Music library through the apple-music MCP server. Use whenever Eike asks for a playlist, a set, a mix or music for an activity."
---

# Playlist Curator (Apple Music)

Eike listens on Apple Music (Mac + iPhone). The `apple-music` MCP server creates playlists directly in his library, so there is no Spotify and no Tune My Music in the normal flow. Reply in Portuguese (pt-BR).

Tools (server `apple-music`): `auth_status`, `my_taste_profile`, `similar_artists`, `discover_tracks`, `list_playlist_profiles`, `plan_playlist`, `artist_top_songs`, `search_songs`, `resolve_tracks`, `order_tracks`, `create_playlist`, `add_tracks_to_playlist`, `list_playlists`, `get_playlist_tracks`, `insert_tracks_into_playlist`, `reorder_playlist`, `sync_playlist`, `remove_tracks_from_playlist`, `resequence_playlist`, `rename_playlist`, `export_playlist_text`.

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

Call `my_taste_profile` once (cached). Use his top artists and heavy rotation to suggest reference artists if he has none, and to recognize what he already listens to a lot.

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

1. **Known share**: for each core artist, call `artist_top_songs` (limit 10) to see what exists and what is popular.
2. **New share**: call `discover_tracks` with the reference artists as `seed_artists`. It returns top songs of **similar artists from Apple's catalog** that he does **not** already know (not in his library or recently played) and has not disliked. Prefer these over discoveries from memory. Use `similar_artists` to explore one artist further.
3. Draft the full list, one line per track (`Artist1, Artist2 - Title (Remix)`), grouped by the act you intend.
4. Call `resolve_tracks` with the whole list (batches of up to 40 lines). Rules:
   - Use only `matched` tracks. Review `low_confidence` ones (candidates are listed) and either pick a candidate with `search_songs` or drop it.
   - `not_found`: replace it with another track by the same artist (check `artist_top_songs`) or drop it. Never invent ids.
   - **`disliked: true`: drop it.** He rated it "dislike".
   - Duplicates (radio edit vs original) are removed automatically. Keep the original or extended mix for DJ sets and the radio edit for workouts.
5. **Check the real known/new ratio** in the summary (`known`, `new`, `known_ratio`). Each track has `familiarity` known/new, taken from his library and listening history. If the ratio is off from what he asked (e.g. 40/60), swap tracks using `discover_tracks` (more new) or `artist_top_songs` of his top artists (more known).
6. Resolve replacements until the list is clean and the total duration is within ±5% of the plan.

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

## 8. Edit an existing playlist (keep it flowing)

Every edit goes straight to his Apple Music library. Never ask him to drag tracks by hand.

1. Find the playlist with `list_playlists`, then read the current order with `get_playlist_tracks` (each track has an `id`). Infer the purpose from the playlist (name, description, how it was built) and confirm it in one line if unsure.
2. **Adding tracks (the usual case): insert each new track where it fits, never reshuffle.**
   - Verify the new tracks with `resolve_tracks`.
   - Estimate `bpm` and `energy` for the new tracks **and** for the tracks already in the playlist (`current_estimates`, ids from `get_playlist_tracks`). The better the estimates, the better the placement.
   - Call `insert_tracks_into_playlist` with the purpose, `new_tracks` and `current_estimates`. The tracks already in the playlist keep their exact order, and each new track goes into the slot that best keeps the vibe: the energy curve, smooth BPM steps and artist spacing. Pin `act` (e.g. "Cooldown") when he says where a track should go.
   - Tell him where each one landed (`inserted`: position, act, between which tracks).
3. **Removing tracks**: `remove_tracks_from_playlist` with the ids. The rest keeps its order. Confirm which tracks went out.
4. Other changes:
   - **Reorganize everything from scratch** (only when he asks): `resequence_playlist` with the full set and estimates.
   - **Exact order he dictates**: `reorder_playlist` (same tracks) or `sync_playlist` (exact final list).
   - **Name or description**: `rename_playlist`.
   - `add_tracks_to_playlist` only appends at the end. Use it only if he explicitly wants the tracks at the end.
5. Read the result:
   - `status: applied` with `verified_order: true` means the library now matches. Report what changed.
   - `status: needs_decision` means Apple refused editing this playlist in place. Nothing was removed or reordered, and any new tracks were only appended at the end. **Never recreate on your own.** Show him `decision.changes`, the `manual_steps` (move X right after Y, remove Z) and the `recommendation`, then ask with AskUserQuestion: "Recriar a playlist (mesmo nome e ordem, link novo; a antiga é apagada)" or "Ajusto manualmente com os passos". For few changes (`size: small`), recommend manual. For many (`size: large`), recommend recreating. Only if he chooses to recreate, call the same tool again with `allow_rebuild: true` and give him the new link.
   - Pass on any `warnings` (for example tracks without estimates).

## 9. Follow-up

Offer a deeper second playlist focused on the artists he liked most after a week or two of listening, or an extension of this one using `resequence_playlist`.
