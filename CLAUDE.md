# CLAUDE.md — project context for Claude Code

Personal MCP server that lets Claude create and maintain Apple Music playlists. Runs **only on the owner's Mac** (stdio MCP server for Claude Desktop; phone access through Claude Dispatch). Owner: Eike. Single user, zero cost.

## Read first
- `docs/decision-guide.md` — the owner's decision guide (source of truth, Portuguese). Items marked **[GATE]** need the owner's explicit approval; **[VERIFICAR]** must be confirmed before implementing.
- `docs/audit.md` — what exists, what is unverified, divergences from the guide, validation log.
- `docs/risk-assessment.md` — Apple account risk and the chosen approach (**hybrid with mitigations**).
- `docs/adr/` — architecture decisions.

## Hard rules
1. **Never print, log, commit or paste secrets**: the `media-user-token`, the developer JWT, `credentials.json`, `.env`, ntfy topics, API keys. When showing command output, redact anything that looks like a token.
2. **Protect the owner's Apple account.** Do not run write operations against Apple Music unless the owner asked for them in this session (`doctor --write` creates and deletes one test playlist). Keep request volume low; never loop over hundreds of API calls.
3. **No paid services or APIs.** Scheduled jobs are deterministic code (no LLM calls).
4. Everything in **English** (code, identifiers, commits, PRs, issues, docs) except `README.pt-BR.md`.
5. **Git flow:** branch from `develop` (`feat/…`, `fix/…`, `docs/…`, `chore/…`) → PR into `develop` using the PR template → CI green → merge. `develop` → `main` releases are approved by the owner. Never push directly to `main` or `develop`. Conventional Commits.
6. Ask before deviating from the guide; raise objections instead of working around decisions.

## Commands
```bash
uv venv && uv pip install -e ".[dev]"   # dev environment
uv run pytest -q                         # tests (offline, fake Apple API)
uv run ruff check . && uv run ruff format --check .
uv tool install --force .                # install the `apple-music-mcp` CLI
apple-music-mcp status                   # masked credentials
apple-music-mcp doctor [--write]         # live checks against Apple
```
`apple-music-mcp login` asks for a hidden token: the **owner runs it in Terminal**, not through Claude Code.

## Layout
`src/apple_music_mcp/apple/` Apple clients (transport, tokens, catalog, library, taste) · `curation/` profiles, planner, sequencer, familiarity · `editing.py`, `refresh.py`, `operations.py` · `tools/` MCP tools · `commands/` CLI · `tests/fake_apple.py` stateful fake API.
