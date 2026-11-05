# 0005. Edit playlists through strategies

**Status:** Accepted

## Context
Playlists evolve. Tracks get added, some get dropped, and the set must be re-sequenced. The web API has no "move track" or "replace list" endpoint. Open-source clients show that it can append tracks, remove a song from a playlist (every occurrence), and rename or delete playlists. Two different removal request shapes are in use, and none of this is documented.

## Decision
`PlaylistEditor.sync(playlist, desired_order)` makes a playlist match an exact order. It does that with strategies tried in order:
1. **In place**: keep the same playlist and link. Keep the longest common prefix, remove the tail and re-append it in the new order. Append-only changes never delete anything. Snapshot first, and restore the original order if anything fails midway. If restoring fails too, say so explicitly.
2. **Rebuild**: create a playlist with the same name and description in the new order, verify it, then delete the old one (or rename it "<name> (old)" if deletion is refused). The link changes and the result says so.

The editor reads the playlist back to report `verified_order`. It remembers the working strategy in `~/.config/apple-music-mcp/state.json`. `LibraryService.remove_entry` tries both removal shapes and remembers the accepted one. The MCP tools on top are `reorder_playlist`, `sync_playlist`, `remove_tracks_from_playlist`, `resequence_playlist` (order engine + sync) and `rename_playlist`.

### Recreating requires confirmation (owner decision)
The rebuild strategy never runs on its own. Every edit tool defaults to `allow_rebuild=false`. When Apple refuses an in-place edit, the editor makes no destructive change. It only appends new tracks so they are not lost, and returns `status: needs_decision` with the number of changes, a size classification (small ≤ 5 changes, otherwise large), minimal manual steps and a recommendation: manual for small changes, recreate for large ones. The manual steps come from a longest increasing subsequence, so only the tracks that really need moving are listed. The skill asks the user. Only an explicit "recreate" re-runs the tool with `allow_rebuild=true`, which deletes the old playlist after the new one is verified.

### Adding tracks: insert, don't reshuffle
Adding tracks must not disturb a playlist the user already likes. `Sequencer.insert` keeps the existing order fixed. It places new tracks one at a time, always taking the (track, slot) pair with the smallest cost increase on the same cost function as `order_tracks`: energy curve, BPM steps, energy jumps, artist spacing and pinned acts. The result is synced in place. Only the tail after the first insertion point is rewritten, and an insertion near the end touches only a few tracks. The tool is `insert_tracks_into_playlist`. `resequence_playlist` (full reshuffle) is reserved for explicit requests.

## Consequences
- The user never reorders by hand. Edits land in the Music app on the Mac and iPhone.
- Which strategy an account accepts is only known live. `doctor --write` tests a reorder and reports it.
- The rebuild fallback loses the old playlist's link and date added. That trade-off is acceptable for a personal tool, and it is surfaced in the tool result.
