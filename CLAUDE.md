# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`apple-music-mcp`: a stdio MCP server (Python 3.11+, MCP SDK 2.x `MCPServer`) that lets Claude curate playlists and write them directly into the user's Apple Music library via the **web-player session** (scraped developer JWT + user-pasted `media-user-token` cookie, no paid developer account). It ships together with a Claude skill (`skill/playlist-curator/SKILL.md`) that owns the conversation and judgement; the server owns deterministic tools. The skill must never pass a catalog id it did not get from a tool.

## Commands

```bash
uv venv && uv pip install -e ".[dev]"        # setup (CI does exactly this)
uv run pytest -q                              # full suite, offline, ~seconds
uv run pytest tests/test_editing.py::test_name -q   # single test
uv run ruff check . && uv run ruff format --check . # lint + format (line length 110)
python scripts/package_skill.py               # rebuild skill/playlist-curator.zip after editing SKILL.md
apple-music-mcp doctor [--write]              # live checks against real Apple endpoints (needs credentials, macOS)
uv tool install --force --reinstall --no-cache .   # reinstall the CLI after code changes
uv run python scripts/diagnose_token.py       # bounded, masked live diagnostic of developer-token discovery
```

Plain `uv tool install --force .` reuses the cached 0.1.0 wheel, so the installed `apple-music-mcp` keeps running old code. Always add `--reinstall --no-cache` before a live check.

`doctor` without `--write` is read-only. `--write` creates and edits a playlist in the owner's real account, so don't run it unless asked.

CI (`.github/workflows/ci.yml`) runs `ruff check` + `pytest` on Ubuntu/macOS × Python 3.11/3.13, with no credentials.

`tests/test_cli.py` asserts the zip contents equal `SKILL.md` byte-for-byte, so any `SKILL.md` edit must be followed by `scripts/package_skill.py`. The skill is also force-included into the wheel (see `pyproject.toml`).

## Architecture

Layered, with dependency inversion (ADR 0007):

- `apple/`: integration layer, the only code that talks HTTP to Apple. `AmpTransport` handles headers (`Origin`/`Referer: music.apple.com`), auth, retry, 401 → developer-token re-scrape, and pagination. `CatalogService` falls back to the public iTunes Search API (`ITunesSearch`, same catalog ids) when amp-api is unavailable. Errors: `AppleMusicError` › `AuthError` › `TokenScrapeError`.
- Domain logic: `resolver.py` (free-text → catalog id via rapidfuzz, penalizes unrequested versions, reports `not_found` rather than guessing), `curation/` (purpose profiles as data → planner → `Sequencer` greedy + pairwise-swap local search on one cost function), `editing.py` (`PlaylistEditor`), `refresh.py`, `discovery.py`, `bpm/`. Domain code depends on protocols in `ports.py`, not on HTTP.
- `container.py` (`AppContainer`): the **composition root**, the only place objects are wired. Tests inject a fake `httpx` client, a no-op `sleep`, and temp stores through its constructor.
- `tools/`: MCP adapters, one module per domain, each exposing `register(mcp, provider)`; add new modules to `tools/__init__.register_all`. Wrap every tool with `@tool_errors` (`tools/base.py`), which maps exceptions to `ToolError` (an `AUTH:` prefix tells Claude to have the user run `apple-music-mcp login`). The container is built lazily via `ContainerProvider`, so the server starts offline.
- `commands/` + `cli.py`: CLI subcommands (`serve`, `login`, `status`, `doctor`, `install-claude`, `refresh`, `schedule-refresh`, `finish-pending`, `set-bpm-key`). `doctor` is a list of `Check` objects.
- `server.py`: `INSTRUCTIONS` string describing the intended tool flow (plan → resolve → order → create; insert vs resequence).

State lives in `~/.config/apple-music-mcp/` (`credentials.json` 0600, `state.json` for remembered edit strategies/removal shapes, BPM cache, etc.).

### Behavioral invariants (from ADRs, enforced by tests)

- **Edits never destroy without consent** (ADR 0005): `PlaylistEditor.sync` tries in-place first (keep common prefix, rewrite tail, snapshot + restore on failure). Rebuild (new playlist, delete old) only runs with `allow_rebuild=true`; otherwise return `status: needs_decision` with manual steps (computed via LIS).
- **Adding tracks inserts, doesn't reshuffle**: `Sequencer.insert` keeps existing order and places each new track at its minimum-cost slot. `resequence_playlist` is only for explicit requests.
- **Refresh never loses a track** (ADR 0010): `plan` is read-only; `apply` only executes confirmed ids; removed tracks are archived to "<name> · Arquivo" and verified before removal. Music-app data is read via `osascript`, read-only.
- **Familiarity mix** (ADR 0011): tracks are bucketed `known_track` / `known_artist_new_track` / `new_artist` using the taste profile; `known_artists_pct ≥ known_tracks_pct`.
- Apple API limits mirrored in the fake: search `limit` ≤ 25, ≤ 100 tracks per add.

## Testing

`tests/fake_apple.py` is a **stateful** in-memory fake of the web player HTML/bundle, amp-api, and iTunes Search, mounted with respx (fixture `fake_apple`). It enforces Apple-like rules (401 bad dev token, 403 bad user token, limits) and has switches for unsupported edit endpoints and midway failures. Add new endpoints to the fake rather than mocking single calls. `conftest.py` autouses `isolated_config` (temp config dir, token env vars cleared); `build_app(creds)` makes a container with no-op sleep. Tests span unit, service, in-process MCP client, a real stdio subprocess (`test_stdio.py`), and e2e simulations. The fake encodes assumptions, and it has been wrong before (Deezer's advanced search syntax, which returns nothing live), so `doctor` is the live safety net. When a live call misbehaves, first make the fake reproduce the real response, then fix the code.

`tests/test_secrets.py` fails CI if any tracked file contains a JWT, private key, `media-user-token` value, or filled token variable, or if `.gitignore` stops blocking credential paths. Build token-shaped fixtures at runtime instead of committing literals (see `make_jwt` / `jwt_factory` in `conftest.py`).

## Conventions

- Everything is in English (code, commits, PRs, ADRs), except `README.pt-BR.md`. User-facing playlist names and suffixes such as "· Descobertas" / "· Arquivo" are pt-BR by design.
- Conventional Commits for commits and PR titles; branches `feat/…`, `fix/…`, `docs/…`, `chore/…`, `refactor/…`. Flow: issue → branch → PR (templates in `.github/`) → green CI.
- Significant design decisions get an ADR in `docs/adr/` (indexed in `docs/adr/README.md`).
- Keep modules small and focused (ADR 0007 targets < 300 lines); new profiles, checks, tools, and edit strategies should be additions, not edits.
