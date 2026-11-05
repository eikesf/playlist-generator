# 0002. Claude skill for judgement, local MCP server for execution

**Status:** Accepted

## Context
The first version was a Claude skill only. Claude searched Spotify through its MCP connector, wrote a text file, and the user imported it with Tune My Music. That had three problems: the Spotify `generate_playlist` tool needs Premium, there was a manual import step, and nothing guaranteed that the tracks existed on Apple Music.

Curating needs taste and context (purpose, vibe, reference artists), which is an LLM's strength. Creating playlists needs exact, repeatable API calls, which are a program's strength.

## Decision
- **Skill (`skill/playlist-curator/SKILL.md`)**: the conversation and the judgement. It runs the discovery questions, chooses the purpose, curates, estimates BPM/energy, names the playlist and writes the description.
- **MCP server (`apple-music-mcp`, stdio, runs on the Mac)**: deterministic tools for catalog search, batch verification, planning, sequencing, creating and editing playlists.
- The skill never passes a catalog id it did not get from a tool.

## Consequences
- The user talks to Claude Desktop and the playlist appears in Apple Music. There is no file and no import.
- The server's logic is unit-testable and reusable from Claude Code or any MCP client.
- The server must run locally because it holds the user's token. Creation happens from Claude Desktop on the Mac, and iCloud syncs the result to the iPhone.
