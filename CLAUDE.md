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
uv run mypy                              # type check (src, scripts, tests; config in pyproject.toml)
uv tool install --force --reinstall --no-cache .  # install the CLI (without --reinstall --no-cache uv reuses the cached 0.1.0 wheel)
uv run python scripts/diagnose_token.py  # bounded, masked live diagnostic of developer-token discovery
apple-music-mcp status                   # masked credentials
apple-music-mcp doctor [--write]         # live checks against Apple
```
`apple-music-mcp login` asks for a hidden token: the **owner runs it in Terminal**, not through Claude Code.

## Layout
`src/apple_music_mcp/apple/` Apple clients (transport, tokens, catalog, library, taste) · `curation/` profiles, planner, sequencer, familiarity · `editing.py`, `refresh.py`, `operations.py` · `tools/` MCP tools · `commands/` CLI · `tests/fake_apple.py` stateful fake API.

## Architecture

Layered, with dependency inversion (ADR 0007):

- `apple/`: integration layer, the only code that talks HTTP to Apple. `AmpTransport` handles headers (`Origin`/`Referer: music.apple.com`), auth, retry, 401 → developer-token re-scrape, and pagination. `CatalogService` falls back to the public iTunes Search API (`ITunesSearch`, same catalog ids) when amp-api is unavailable. Errors: `AppleMusicError` › `AuthError` › `TokenScrapeError`.
- Domain logic: `resolver.py` (free-text → catalog id via rapidfuzz, penalizes unrequested versions, reports `not_found` rather than guessing), `curation/` (purpose profiles as data → planner → `Sequencer` greedy + pairwise-swap local search on one cost function), `editing.py` (`PlaylistEditor`), `refresh.py`, `discovery.py`, `bpm/`. Domain code depends on protocols in `ports.py`, not on HTTP.
- `container.py` (`AppContainer`): the **composition root**, the only place objects are wired. Tests inject a fake `httpx` client, a no-op `sleep`, and temp stores through its constructor.
- `tools/`: MCP adapters, one module per domain, each exposing `register(mcp, provider)`; add new modules to `tools/__init__.register_all`. Wrap every tool with `@tool_errors` (`tools/base.py`), which maps exceptions to `ToolError` (an `AUTH:` prefix tells Claude to have the user run `apple-music-mcp login`). The container is built lazily via `ContainerProvider`, so the server starts offline.
- `commands/` + `cli.py`: CLI subcommands (`serve`, `login`, `status`, `doctor`, `install-claude`, `refresh`, `schedule-refresh`, `finish-pending`, `set-bpm-key`). `doctor` is a list of `Check` objects.
- `server.py`: `INSTRUCTIONS` string describing the intended tool flow (plan → resolve → order → create; insert vs resequence).

State lives in `~/.config/apple-music-mcp/`: `credentials.json` (0600) and `apple-music-mcp.db` (SQLite, 0600). The database schema is `db/schema.py`, mirroring the approved ERD in `docs/data-model.md`; every schema change needs an Alembic migration in `db/migrations/versions/` (`tests/test_db.py` checks they match). `Database.open()` applies pending migrations. Small state (remembered edit strategies, removal shapes, caches) goes through `KeyValueStore` (`ports.py`), backed by the `setting` table; a legacy `state.json` is imported once and renamed to `state.json.imported`.

### Behavioral invariants (from ADRs, enforced by tests)

- **Edits never destroy without consent** (ADR 0005): `PlaylistEditor.sync` tries in-place first (keep common prefix, rewrite tail, snapshot + restore on failure). Rebuild (new playlist, delete old) only runs with `allow_rebuild=true`; otherwise return `status: needs_decision` with manual steps (computed via LIS).
- **Adding tracks inserts, doesn't reshuffle**: `Sequencer.insert` keeps existing order and places each new track at its minimum-cost slot. `resequence_playlist` is only for explicit requests.
- **Refresh never loses a track** (ADR 0010): `plan` is read-only; `apply` only executes confirmed ids; removed tracks are archived to "<name> · Arquivo" and verified before removal. Music-app data is read via `osascript`, read-only.
- **Familiarity mix** (ADR 0011): tracks are bucketed `known_track` / `known_artist_new_track` / `new_artist` using the taste profile; `known_artists_pct ≥ known_tracks_pct`.
- Apple API limits mirrored in the fake: search `limit` ≤ 25, ≤ 100 tracks per add.

## Testing

`tests/fake_apple.py` is a **stateful** in-memory fake of the web player HTML/bundle, amp-api, and iTunes Search, mounted with respx (fixture `fake_apple`). It enforces Apple-like rules (401 bad dev token, 403 bad user token, limits) and has switches for unsupported edit endpoints and midway failures. Add new endpoints to the fake rather than mocking single calls. `conftest.py` autouses `isolated_config` (temp config dir, token env vars cleared); `build_app(creds)` makes a container with no-op sleep. Tests span unit, service, in-process MCP client, a real stdio subprocess (`test_stdio.py`), and e2e simulations. The fake encodes assumptions, and it has been wrong before (Deezer's advanced search syntax, which returns nothing live), so `doctor` is the live safety net. When a live call misbehaves, first make the fake reproduce the real response, then fix the code.

`tests/test_secrets.py` fails CI if any tracked file contains a JWT, private key, `media-user-token` value, or filled token variable, or if `.gitignore` stops blocking credential paths. Build token-shaped fixtures at runtime instead of committing literals (see `make_jwt` / `jwt_factory` in `conftest.py`).
