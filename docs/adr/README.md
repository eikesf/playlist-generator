# Architecture Decision Records

Short records of the decisions that shaped this project: the context, the decision and its consequences. Format: [Michael Nygard's ADR template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

| # | Decision | Status |
|---|---|---|
| [0001](0001-web-player-auth.md) | Authenticate with the music.apple.com web-player session, not a paid developer key | Accepted |
| [0002](0002-skill-plus-mcp-server.md) | Split the work: Claude skill (judgement) + local MCP server (execution) | Accepted |
| [0003](0003-apple-catalog-as-source.md) | Use the Apple Music catalog as the only source of truth; iTunes Search as fallback | Accepted |
| [0004](0004-estimated-features-deterministic-sequencing.md) | The LLM estimates BPM/energy; a deterministic algorithm sequences | Accepted |
| [0005](0005-playlist-edit-strategies.md) | Edit playlists through strategies: in-place with restore, rebuild as fallback | Accepted |
| [0006](0006-testing-with-a-stateful-fake.md) | Test against a stateful fake Apple API; verify live with `doctor` | Accepted |
| [0007](0007-stack-and-layered-architecture.md) | Python, MCP SDK 2.x, httpx, rapidfuzz; layered SOLID architecture | Accepted |
| [0008](0008-local-credentials-and-secret-scanning.md) | Credentials stay on the Mac (0600) and a secret scan guards the repo | Accepted |
| [0009](0009-stacked-pull-requests.md) | Deliver in small stacked pull requests, one issue each | Accepted |
| [0010](0010-refresh-never-lose-a-track.md) | Refresh never loses a track: staging playlist, archive, explicit confirmation | Accepted |
| [0011](0011-familiarity-mix.md) | Familiarity mix: known songs and known artists are separate targets | Accepted |
