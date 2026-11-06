# Phase 3 — Live validation runbook (owner, on the Mac)

Phase 3 was built and tested offline (CI, fake Apple API). These steps check it on the real Mac and account. Every write to Apple Music is called out. Paste outputs back to Claude after masking anything that looks like a token; the commands never print tokens.

**Expect little history at first.** The daily snapshot started on 2026-11-06. Until there are 14 days of play/skip history (around 2026-11-20), refresh proposals contain only tracks you marked "não gostei", and their notes say why. This is by design.

## 1. Update

```bash
cd ~/Personal_DEV_Projects/playlist-generator
git checkout develop && git pull
uv tool install --force --reinstall --no-cache .
```

Restart Claude Desktop so it loads the new tools.

## 2. New settings (optional)

Compare your `~/.config/apple-music-mcp/.env` with `.env.example`. New keys, all optional with defaults: `REFRESH_WEEKDAY`, `REFRESH_TIME`, `REFRESH_WINDOW_DAYS`, `REFRESH_MIN_HISTORY_DAYS`, `REFRESH_MIN_DAYS_IN_PLAYLIST`, `REFRESH_MIN_SKIPS`, `REFRESH_SKIP_RATIO`, `PROPOSAL_TTL_DAYS`, `DISCOVERIES_SIZE`, `PROMOTE_MIN_PLAYS`, `PROMOTE_MAX_SKIP_RATIO`, `PROMOTE_WEEKLY_FRACTION`, `PROMOTE_WEEKLY_MAX`. Leave `REFRESH_AUTO_APPLY=false`. Don't paste the file here (it holds the ntfy topic).

## 3. Turn the refresh on for one playlist (in Claude)

In Claude Desktop: "liga o refresh semanal da <playlist>". Claude calls `update_managed_playlist` with `refresh_enabled: true`, and `adopt_playlist` first if the playlist is not managed yet. No write to Apple.

## 4. Scheduled jobs

```bash
apple-music-mcp schedule install
```

Expected: 6 jobs, including `refresh_plan  Mon 09:00`. If you had used `schedule-refresh` before, run `apple-music-mcp schedule-refresh --playlist "<name>"` once to remove the old agent.

## 5. First proposal now (read-only)

```bash
apple-music-mcp run refresh_plan
```

Expected: `refresh_plan: ok {... 'proposals': [{'playlist': '<name>', 'remove': N, 'add': M}] ...}`, plus an ntfy notification "Refresh pronto para <name>: N saem, M entram" if N > 0. Nothing changes in Apple Music. Running it again the same day creates nothing new.

## 6. Approve from the phone (Dispatch), or the Mac

Ask Claude: "quais propostas estão pendentes?". Check that:
- each removal shows its reason (`disliked` for now), plays/skips, and its replacement;
- you can accept some items and reject others with a reason;
- **only after you accept**, `apply_proposal` changes the playlist. Removed tracks appear in "<name> · Arquivo" first. **This is a write; do it only if you agree with the items.**

## 7. Weekly Discoveries (Claude Desktop scheduled task)

1. In Claude: "liga as Descobertas da semana da <playlist>".
2. Create the scheduled task as described in [`docs/scheduled-tasks/weekly-discoveries.md`](../scheduled-tasks/weekly-discoveries.md) (Monday 09:30, only on this computer, folder `~/apple-music-tasks`).
3. Use "Run now" once. **This writes** one playlist, "<name> · Weekly Discoveries", with about 30 tracks. The main playlist must stay unchanged.

Report: the number of tracks, the dropped tracks and their reasons, and whether the picks fit the playlist.

## 8. Promotion (one week later)

Love (♥) a few tracks in Weekly Discoveries during the week. On Monday, after `refresh_plan`, you should get "Promoção pronta para <name>: N faixas…", with N at most 20 % of the main playlist. Approve it in Claude: the tracks enter the main playlist where they fit and leave Weekly Discoveries. **This is a write.**

## 9. What to send back

- Output of steps 4 and 5 (masked).
- Did the ntfy alert arrive? Did Dispatch show the proposal?
- Steps 7 and 8 when they happen.
