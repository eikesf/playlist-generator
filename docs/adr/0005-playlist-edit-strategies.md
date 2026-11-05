# 0005. Edit playlists through strategies

**Status:** Accepted

## Context
Playlists evolve. Tracks get added, some get dropped, and the set must be re-sequenced. The web API has no "move track" or "replace list" endpoint. Open-source clients show that it can append tracks, remove a song from a playlist (every occurrence), and rename or delete playlists. Two different removal request shapes are in use, and none of this is documented.

## Decision
`PlaylistEditor.sync(playlist, desired_order)` makes a playlist match an exact order. It does that with strategies tried in order:
1. **In place**: keep the same playlist and link. Keep the longest common prefix, remove the tail and re-append it in the new order. Append-only changes never delete anything. Snapshot first, and restore the original order if anything fails midway. If restoring fails too, say so explicitly.
2. **Rebuild**: create a playlist with the same name and description in the new order, verify it, then delete the old one (or rename it "<name> (old)" if deletion is refused). The link changes and the result says so.

The editor reads the playlist back to report `verified_order`. It remembers the working strategy in `~/.config/apple-music-mcp/state.json`. `LibraryService.remove_entry` tries both removal shapes and remembers the accepted one. The MCP tools on top are `reorder_playlist`, `sync_playlist`, `remove_tracks_from_playlist`, `resequence_playlist` (order engine + sync) and `rename_playlist`.

## Consequences
- The user never reorders by hand. Edits land in the Music app on the Mac and iPhone.
- Which strategy an account accepts is only known live. `doctor --write` tests a reorder and reports it.
- The rebuild fallback loses the old playlist's link and date added. That trade-off is acceptable for a personal tool, and it is surfaced in the tool result.
