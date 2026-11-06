# Weekly Discoveries: Claude Desktop scheduled task

Guide §7.11 asks for a weekly playlist of about 30 new tracks per main playlist, chosen by the AI. Scheduled jobs on the Mac cannot call an LLM (guide §10, no paid APIs), so the choosing runs in a **Claude Desktop scheduled task**, which is covered by the subscription. The task runs on this Mac and reaches the local `apple-music` MCP server; this was verified on 2026-11-05 (audit §7, risk assessment §2.5).

Deterministic code does the rest: `weekly_discoveries_context` gathers the facts and candidate pools, and `submit_weekly_discoveries` validates every id (catalog, blacklist, dislikes, already heard, main playlist, Arquivo, earlier rejections, size) before it writes to "<name> · Weekly Discoveries". The main playlist is never touched. Promotions from the weekly playlist into the main one are separate proposals that you approve, like the refresh.

## One-time setup

1. In a conversation, turn it on per playlist: "liga as Descobertas da semana para a <playlist>". This calls `update_managed_playlist` with `discoveries_enabled: true`.
2. In Claude Desktop, create a scheduled task:
   - **Schedule:** weekly, Monday **09:30**. That is after the 09:00 `refresh_plan` job, which by then has recorded what you liked from last week's batch.
   - **Only on this computer:** on. Keep the Mac awake (or allow "keep awake").
   - **Working folder:** the empty `~/apple-music-tasks` folder.
   - **Prompt:** paste the block below.

## Prompt to paste

```text
Weekly Discoveries for my Apple Music playlists (apple-music MCP server). Reply in Portuguese.

1. Call weekly_discoveries_context without arguments to get the playlists with Weekly Discoveries on.
   If there are none, stop and say so.
2. For each playlist:
   a. Call weekly_discoveries_context with its playlist_id. Read the profile, the mix note, the top
      artists, the genres, my dislikes (with reasons), the blacklist, the tracks to avoid and last
      week's batch.
   b. Choose about target_size tracks (within size_range) that I have never heard and that fit the
      playlist's purpose and vibe. Use the candidate pools first (new_artists, deep_cuts). For more,
      use discover_tracks, deep_cuts, similar_artists or artist_top_songs, and verify every extra
      pick with resolve_tracks (matched only, never invent ids). Follow the mix note: deep cuts by
      artists I know vs new artists. Give each new artist 2-3 tracks so I can judge them. Do not
      repeat last week's batch.
   c. Call submit_weekly_discoveries with the playlist_id, the catalog ids and one line of notes
      (this week's theme). If it refuses the batch (size), replace the dropped tracks and submit again.
3. Finish with a short summary per playlist: link of the weekly playlist, how many tracks, 3-5
   highlights, the dropped tracks with their reasons, and a reminder that I can love tracks I like
   (they become promotion proposals I approve).
Never change the main playlists, never add to the blacklist, never remove anything.
```

## What happens next

- During the week you listen to "<name> · Weekly Discoveries" and love (♥) what you like.
- The Monday `refresh_plan` job turns liked tracks into a **promotion** proposal, capped at 20 % of the main playlist and 30 tracks a week, and sends an ntfy alert. You approve it in a conversation, like the refresh.
- Liked tracks waiting for approval stay in the weekly playlist when the new batch arrives.
