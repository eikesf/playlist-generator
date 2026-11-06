---
name: "playlist-curator"
description: "Curates a playlist for a purpose (study, workout, running, party, DJ-style techno set, chill, sleep, genre exploration), verifies every track in the Apple Music catalog, sequences it along the purpose's energy curve, shows a preview and, once approved, creates it directly in Eike's Apple Music library through the apple-music MCP server. Use whenever Eike asks for a playlist, a set, a mix or music for an activity."
---

# Playlist Curator (Apple Music)

Eike listens on Apple Music (Mac + iPhone). The `apple-music` MCP server creates playlists directly in his library, and that is the only way playlists are made: never Spotify, Tune My Music, text files or any other importer. Reply in Portuguese (pt-BR).

Tools (server `apple-music`): `auth_status`, `start_session`, `record_answer`, `preview_playlist`, `reject_preview_tracks`, `approve_preview`, `adopt_playlist`, `list_managed_playlists`, `update_managed_playlist`, `playlist_history`, `dislike_track`, `resolve_dislike`, `list_dislikes`, `blacklist_add`, `blacklist_remove`, `blacklist_list`, `my_taste_profile`, `known_tracks`, `deep_cuts`, `similar_artists`, `discover_tracks`, `list_playlist_profiles`, `profile_curve`, `create_profile`, `delete_profile`, `plan_playlist`, `artist_top_songs`, `search_songs`, `resolve_tracks`, `lookup_bpm`, `order_tracks`, `create_playlist`, `add_tracks_to_playlist`, `list_playlists`, `get_playlist_tracks`, `insert_tracks_into_playlist`, `reorder_playlist`, `sync_playlist`, `remove_tracks_from_playlist`, `resequence_playlist`, `rename_playlist`, `plan_playlist_refresh`, `apply_playlist_refresh`, `list_pending_proposals`, `show_proposal`, `decide_proposal`, `apply_proposal`, `weekly_discoveries_context`, `submit_weekly_discoveries`, `finish_pending_playlists`.

The one rule above all: **the purpose of the playlist decides everything.** A study playlist, a workout and a techno set differ in energy curve, tempo, track length and vocals. Find out the purpose first, then let the plan drive the curation.

## 0. Preflight

Call `auth_status` once at the start.
- `ready_to_create_playlists: true`: continue.
- Otherwise tell Eike to run `apple-music-mcp login` in the Mac Terminal (the token expires from time to time), and keep curating. At the end, use the fallback in step 7.
- If the `apple-music` tools are missing entirely, the server is not connected. Point him to the README of github.com/eikesf/playlist-generator (`apple-music-mcp install-claude`).

## 1. First question, always: edit or create?

Ask **"Editar uma playlist existente ou criar uma nova?"** before anything else. Then call `start_session` with `intent` (`create` / `edit`), his request in his words and, for edit, the `playlist_id` (find it with `list_playlists` or `list_managed_playlists`).
- A **managed** playlist comes back with its saved purpose, profile and mix: reuse them and don't ask again. If he wants to change the purpose, it is a full redefinition of the profile: confirm, then `update_managed_playlist`.
- `avoid` lists tracks he rejected before, with the reason. Never suggest them again, and read the reasons (they tell you what he dislikes).
- Editing a playlist that is not managed yet: ask its purpose and mix, then `adopt_playlist` so they are remembered.

## 2. Understand what he wants (no limit on questions)

**The goal: a playlist he is very likely to enjoy, built on his real taste, that still brings new songs and new artists.** There is no fixed number of questions: keep refining until you understand what he expects (purpose, mood, artists, mix, duration, restrictions). Use AskUserQuestion (up to 4 questions per round, as many rounds as needed). Skip what he already answered. Save every question and answer with `record_answer`. Only use defaults without asking when he explicitly asks for a quick playlist.

Call `my_taste_profile` once (cached) before asking, to suggest reference artists and recognize what he listens to a lot.

Ask about:
- **Purpose / context**: what he will be doing while listening. Profiles come in families with sub-profiles (focus → study, binaural, epic_soundtrack; workout → hiit, strength; dj_set → melodic_techno, peak_time_techno; plus running, party, chill, sleep, exploration). Pick the most specific one; call `list_playlist_profiles` when unsure and `profile_curve` to show him how the energy will evolve. If nothing fits, agree on a new profile with him and save it with `create_profile` (it stays for future sessions; `delete_profile` removes it).
- **Duration**: always ask, in time or number of tracks. If he skips it, use **5 hours** (300 minutes).
- **The mix, in two levels** (defaults if he doesn't choose: 50 % / 30 %):
  - Level 1, `known_artists_pct`: share of the playlist by artists he already knows. Options: "30% – muitos artistas novos", "50% – equilíbrio (Recommended)", "70% – poucos artistas novos", "100% – só artistas que já ouço".
  - Level 2, `known_tracks_within_known_artists_pct`: among those artists, how many songs he already knows (the rest are songs of theirs he never heard). Options: "10% – quase tudo inédito", "30% – equilíbrio (Recommended)", "50% – metade conhecida", "80% – quase tudo conhecido".
  - "Known" means loved or played 10+ times. Songs played 1–9 times count as neither and are left out by default.
- **Energy curve** when the profile allows a choice, **vocals**, sub-genres to include or avoid, and one reference track that captures the vibe. If an artist name looks like a speech-to-text mangling, confirm the spelling (or check with `search_songs`).
- Explicit content: no filter, never ask.

## 3. Plan

Call `plan_playlist` with the purpose (free text works: "set de techno", "treino hiit", "foco binaural"), `duration_minutes` or `track_count`, `known_artists_pct` and `known_tracks_within_known_artists_pct`. The plan returns:
- the acts with minutes, number of tracks, target energy (1–10) and target BPM per act,
- vocals, max track length, transition rules (max BPM step, max energy step, spacing between tracks by the same artist) and genre guidance,
- `familiarity_mix.counts`: how many tracks go in each bucket:
  - `known_track`: songs he already knows (loved or 10+ plays),
  - `known_artist_new_track`: songs he never heard, by artists he knows (deep cuts),
  - `new_artist`: artists he does not know yet.

Say which profile you picked and why in one line. Fill each act with tracks that match its intent, and spread the buckets across the acts. Known songs work well at the opening and in the peak, and discoveries fit the build and the groove.

Balance:
- Each core artist he named gets 3 to 5 tracks so he can judge their style. One track per artist is not enough.
- New artists: 2–3 tracks for the 4–5 most promising, 1–2 for the rest, so he can actually judge them.
- Draft about 15% more tracks than the plan needs. Some will not be in the catalog.

## 4. Gather per bucket and verify (never from memory alone)

1. **`known_track`**: `known_tracks` with his reference and top artists. These are songs he already knows, with loved ones first, and they are the safest picks for enjoyment. Choose the ones that fit the purpose and vibe.
2. **`known_artist_new_track`**: `deep_cuts` with the artists he knows. These are popular songs by those artists that he has not heard yet. `artist_top_songs` helps explore one artist.
3. **`new_artist`**: `discover_tracks` with the reference artists as `seed_artists`. It returns top songs of **similar artists from Apple's catalog** that he doesn't know and hasn't disliked. Prefer these over discoveries from memory. Use `similar_artists` to explore further. Pick new artists closest to his taste so the discoveries are likely to land.
4. Draft the full list, one line per track (`Artist1, Artist2 - Title (Remix)`), grouped by the act you intend.
5. Call `resolve_tracks` with the whole list (batches of up to 40 lines) **and** his `known_artists_pct` / `known_tracks_within_known_artists_pct`. Rules:
   - Use only `matched` tracks. Review `low_confidence` ones (candidates are listed) and either pick a candidate with `search_songs` or drop it.
   - `not_found`: replace it with another track from the same bucket or drop it. Never invent ids.
   - **`disliked: true`: drop it.** He rated it "dislike".
   - **`blocked`: drop it.** It is on his blacklist.
   - **`familiarity: partly_known`** (1–9 plays, or only in his library): swap it for a known or never-heard track unless he asked for it.
   - Duplicates (radio edit vs original) are removed automatically. Keep the original or extended mix for DJ sets and the radio edit for workouts.
6. **Check the mix**: every track has `familiarity` (its bucket), and `summary.mix_check` compares the real counts with his targets. If it is not `within_tolerance`, follow its `suggestions` (e.g. "replace 2 'known_track' with 'new_artist'") using the bucket tools above, then resolve again.
7. Repeat until the list is clean, the mix is within tolerance and the total duration is within ±5% of the plan.

## 5. Sequence

The Apple catalog has no BPM or energy data. For every verified track, give:
- `title` and `artist`: always, so the real BPM can be looked up,
- `bpm`: your best estimate (used only if no database has the track),
- `energy`: 1 (calm, sparse) to 10 (peak, hardest, biggest drop), relative to the purpose. This is always your judgement,
- optionally `act` to pin a track into an act (e.g. the closing track into "Cooldown").

Call `order_tracks` with the purpose and the tracks. It first looks up **measured BPM** in public databases (GetSongBPM if a key is configured, then Deezer) and replaces your estimate where found, correcting half/double time. Each track reports `bpm_source` (a database name or `estimate`). Then it returns `song_ids_in_order` following the plan's energy curve, with smooth BPM steps, no same artist back to back and pinned acts respected. Read its `warnings`. If it reports big BPM jumps or artist repeats, swap a track and call it again. Do not reorder by hand afterwards. If he asks about tempo, say which values were measured and which estimated. `lookup_bpm` checks tempos on their own.

## 6. Preview, adjust, then create (preview is the default)

1. Decide the name and a description **in English** (decisive, evocative, name max ~40 chars, description ≤ 250 chars).
2. Call `preview_playlist` with the `session_id`, `name`, `description`, `purpose`, duration and mix, and `tracks` in the order from `order_tracks`, each with its `section` (the act) and `familiarity`. **Nothing is created yet.**
3. Show him the preview **by section** (act), with the duration per act, the mix and the new artists by name. On mobile or for long lists, a summary per act plus the highlights.
4. He adjusts ("tira esse artista", "não gosto dessa faixa", "mais energia no meio"):
   - tracks he doesn't want: `reject_preview_tracks` with his reason in his words;
   - build the new version and call `preview_playlist` again with `replaces_proposal_id`;
   - repeat until he is happy.
5. When he approves, call `approve_preview`. It creates the playlist in Apple Music (rejected tracks left out, order kept) and makes it a **managed** playlist that remembers purpose, profile and mix.
6. Only create directly with `create_playlist` when he explicitly asks to skip the preview.
7. Reply with the playlist link (`playlist.url`), a note that it shows up on his iPhone within a few minutes (iCloud Music Library sync), the profile used, the **final mix** (level 1 and level 2, e.g. "50% artistas que você conhece; entre eles, 30% músicas que você já ama"), two alternative names and two descriptions in English, and honest notes on artists with too few tracks and dropped tracks.

## 7. Login expired (everything stays inside Apple Music)

There is **no export file and no third-party importer** (no Tune My Music). Playlists are only ever created by the `apple-music` tools, directly in his library.

If `create_playlist` fails with `AUTH`, the playlist was saved as **pending**: same verified tracks, same order, same name and description, so nothing is lost. Tell him to run `apple-music-mcp login` in the Mac Terminal. When he confirms, call `finish_pending_playlists` and give him the link. To drop a pending idea, pass its name in `discard`.

## 8. Edit an existing playlist (keep it flowing)

Every edit goes straight to his Apple Music library. Never ask him to drag tracks by hand.

1. Find the playlist with `list_playlists` (or `list_managed_playlists`), then read the current order with `get_playlist_tracks` (each track has an `id`). For a managed playlist, use the purpose and mix `start_session` returned; otherwise infer the purpose and confirm it in one line, then `adopt_playlist`.
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

## 8b. "Não gostei" and the blacklist

- **"Não gostei"** about a track: ask **why**, then `dislike_track` with the reason (and the `playlist_id` if it is in one). It is only a suggestion to remove: confirm, remove with `remove_tracks_from_playlist`, then `resolve_dislike` (`removed` or `kept`). **Never block the artist because of one track**: tracks by the same artist can have very different vibes. Use `list_dislikes` reasons to steer future picks.
- **Blacklist** only when he explicitly asks ("nunca mais coloca X"): `blacklist_add` with `scope` (artist or track) and his request verbatim. Never infer it from a dislike. `blacklist_list` shows it; `blacklist_remove` lifts an entry when he asks. Blocked tracks are dropped automatically everywhere.
- Managed playlists keep their history: `playlist_history` shows what was added, removed or moved, when and why.

## 9. Weekly refresh: proposals he approves (Mac or Dispatch)

**Golden rule: never remove a track from the main playlist unless he named it or confirmed it.** He could lose a song he liked and not remember its name.

Playlists with the refresh on (`update_managed_playlist` with `refresh_enabled: true`; `refresh_weekday` / `refresh_time` change the slot, default Monday 09:00) get a **proposal** every week from a job on his Mac. Nothing changes until he approves. He gets a phone notification "Refresh pronto para <playlist>: X saem, Y entram".

1. When he mentions the notification or asks what is pending: `list_pending_proposals`, then `show_proposal`.
2. Show each proposal **by name**, each removal next to its replacement:
   - `remove` items: `why` is `often_skipped` (with skips/plays in the window), `not_played` (no plays in the last weeks while the playlist was played) or `disliked`. Favorites, loved tracks and recent additions are never proposed.
   - `add` items: the replacement (`replaces_item_id`), from the same familiarity bucket so the mix holds, never from the blacklist, his dislikes or tracks he rejected before.
   - Read the `notes`: with little play history (the daily snapshots started recently) there is no rotation yet, only dislikes. Say so plainly.
3. Ask what to accept (AskUserQuestion, multi-select; on the phone a short numbered list is fine). Record it with `decide_proposal`: `accept_item_ids` and `reject` with his reason in his words (rejected additions are avoided in future sessions; a rejected removal just means "keep it"). `accept_all` / `reject_all` cover what is still pending.
4. Once `approved`, call `apply_proposal`. Pass `estimates` (bpm/energy) for the new tracks and, ideally, the current ones (`get_playlist_tracks`) so each new track lands where it fits. Removed tracks are first copied to "<name> · Arquivo" and verified. On `needs_decision`, follow section 8.
5. Report what left, what came in and where, and remind him the Arquivo keeps everything that left.

On request, outside the weekly slot: `plan_playlist_refresh` builds the same kind of proposal now (or returns the open one). `apply_playlist_refresh` with `remove_ids` removes tracks he names directly (archived first).

## 9b. Weekly Discoveries ("Descobertas da semana")

For managed playlists with `discoveries_enabled` (turn it on with `update_managed_playlist` only when he asks), a weekly playlist "<name> · Weekly Discoveries" gets about 30 tracks he never heard, chosen by you. It normally runs from his Claude Desktop scheduled task (Monday 09:30; prompt in `docs/scheduled-tasks/weekly-discoveries.md` of the repository):

1. `weekly_discoveries_context` without arguments lists the playlists; with a `playlist_id` it returns the profile, mix note, top artists, genres, dislikes, blacklist, tracks to avoid, last week's batch and candidate pools (`new_artists`, `deep_cuts`).
2. Choose about `target_size` tracks (inside `size_range`) by vibe and purpose. Verify extra picks with `resolve_tracks`. Give each new artist 2-3 tracks. Don't repeat last week.
3. `submit_weekly_discoveries` (a **write**: only from the scheduled task or when he asks) validates every id and fills the weekly playlist. Read `dropped` (reason per track) and, if the batch was refused for size, replace and submit again. The main playlist is never touched.

Tracks he **likes** there (loved, or played at least 3 times with under 25 % skips since the batch arrived) become a weekly **promotion** proposal, at most 20 % of the main playlist and never more than 30 tracks a week. He gets "Promoção pronta para <playlist>". Review and approve it exactly like the refresh (section 9): `show_proposal` (`why`: `loved` or `played Nx, skipped Mx`), `decide_proposal`, `apply_proposal`. Promoted tracks are inserted where they fit (existing order kept) and leave the weekly playlist. Liked tracks waiting for approval stay in the weekly playlist when the next batch arrives.

## 10. Follow-up

Offer a deeper second playlist focused on the artists he liked most after a week or two of listening, or a refresh as above.
