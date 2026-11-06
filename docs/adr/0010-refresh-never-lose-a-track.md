# 0010. Refresh never loses a track

**Status:** Accepted

## Context
Playlists get stale, so the owner wants a weekly refresh. The owner set two constraints. Rotating ~20% of tracks is acceptable only if the least-listened tracks can actually be identified. And **no track may leave the main playlist unless the owner names or confirms it**, because a liked song could be lost and its name forgotten. The Apple Music web API has no play counts. The Music app on the Mac does, through AppleScript.

## Decision
- **Two phases.** `plan` is read-only for the main playlist. `apply` only executes what the user confirmed (`remove_ids`, `promote`).
- **Least-played detection** reads `played count`, `skipped count`, `date added` and `favorited`/`loved` from the Music app (`osascript`, read-only). Tracks added in the last 14 days and favorites are never candidates. If play counts don't distinguish tracks, nothing is suggested.
- **Dislikes** come from API ratings and the Music app's `disliked` flag. They are suggested for removal, never removed automatically.
- **Discoveries** (similar artists, not known to the user) go to a separate staging playlist, "<name> · Descobertas". Tracks the user loved or played ≥ 3 times there become promotion candidates. A promoted track is inserted at its best position (ADR 0005) and leaves staging.
- **Archive before remove.** Removed tracks are first added to "<name> · Arquivo", and the copy is verified before anything is removed.
- **Scheduling** is a launchd agent on the Mac that runs the read-only plan weekly and shows a notification. Decisions always come back to a conversation with Claude.

## Consequences
- No silent loss is possible. The worst case is a track that is both in the Arquivo and still in the main playlist.
- Play-count based rotation works only on the Mac and needs a one-time Automation permission. Elsewhere the refresh still stages discoveries and flags dislikes.
- AppleScript property names (`favorited` vs the older `loved`) differ between macOS versions. The script tries both.

## Amendment (Phase 3, 2026-11-06)
- **The plan is a proposal.** `build_plan` writes nothing to Apple Music. It stores a `refresh` proposal (data model decision 6) whose items are removals and their one-for-one replacements (`replaces_item_id`). The owner decides per item in a conversation, and `apply` executes only `accepted` items of an `approved` proposal. Archive-before-remove is unchanged.
- **Evidence from snapshots.** "Least played" became plays and skips over a window (42 days by default), measured from the daily `play_snapshot` deltas instead of all-time counters. With fewer than 14 days of history, no rotation is proposed and the proposal says why.
- **Discoveries moved out of the refresh.** The plan no longer stages discoveries, which was a write from a scheduled job. Claude curates "<name> · Weekly Discoveries" in a Desktop scheduled task. Promotions are separate, capped proposals (guide §7.11) that the owner approves.
- **Scheduling** is the `refresh_plan` launchd job (Monday 09:00 by default, per playlist overridable), with an ntfy alert. The old per-playlist `schedule-refresh` agent is retired.
