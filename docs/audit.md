# Phase 0 — Code audit

**Date:** 2026-11-05 · **Scope:** `main` at `a68b584` plus open PRs #31 (familiarity mix) and #33 (Apple-only, pending playlists) · **Author:** Claude Code

> Status: the code has **never been executed against the real Apple Music API**. The build sandbox blocks Apple, Deezer and GetSongBPM. Every "works" below means "passes tests against `tests/fake_apple.py`". The fake encodes our *assumptions* about the API.

## 1. What is implemented

| Area | Modules | State |
|---|---|---|
| Developer-token scraping (web player bundle → JWT, cache, re-scrape on 401) | `apple/tokens.py` | Implemented, unit-tested, **unverified live** |
| HTTP transport (headers, 429/5xx retry with jitter + `Retry-After`, pagination) | `apple/transport.py`, `apple/http.py` | Implemented. **No rate limiter**, no request cache |
| Catalog: search songs/artists, top songs, songs by id, similar artists | `apple/catalog.py` | Implemented. `similar-artists` view **unverified** |
| iTunes Search fallback (public API) | `apple/itunes.py` | Implemented |
| Library: storefront, list/get/create/rename/delete playlists, add/remove tracks | `apple/library.py` | Implemented. Remove uses 2 guessed request shapes; PATCH/DELETE **unverified** |
| Taste: library songs, recently played, heavy rotation, ratings | `apple/taste.py` | Implemented. **Likely bug:** recently played requested with `limit=30`, community reports say > 10 returns an error → silently yields no data (see §3) |
| Track resolver (fuzzy match, version penalties, dedupe) | `resolver.py`, `text.py` | Implemented, well tested |
| Purpose profiles (8), planner, sequencer (greedy + incremental local search) | `curation/*` | Implemented, well tested |
| Familiarity mix (3 buckets, mix check) | `curation/familiarity.py`, `discovery.py` | PR #31. **Diverges from the guide** (§4) |
| Measured BPM (GetSongBPM, Deezer) + cache | `bpm/*` | Implemented, **unverified live** |
| Playlist editing: in-place with snapshot/restore, rebuild only on confirmation, manual steps | `editing.py` | Implemented, well tested |
| Insert at best position without reshuffling | `curation/sequencer.py`, `operations.py` | Implemented |
| Weekly refresh (plan/apply, staging "· Descobertas", archive "· Arquivo") | `refresh.py`, `local_music.py` | Implemented. **Diverges from the guide** (§4) |
| Music app stats via AppleScript (plays, skips, favorites, dislikes) | `local_music.py` | Implemented, **never run** (`favorited` vs `loved` property name unverified) |
| Pending playlists after an expired login | `operations.py` | PR #33 |
| CLI: `serve`, `login [--browser]`, `status`, `doctor [--write]`, `install-claude`, `set-bpm-key`, `refresh`, `schedule-refresh`, `finish-pending` | `cli.py`, `commands/*` | Implemented |
| MCP server: 25 tools (stdio) | `server.py`, `tools/*` | Implemented; real stdio handshake tested |
| CI: ruff + pytest on Ubuntu/macOS × Py 3.11/3.13; secret scan | `.github/workflows/ci.yml`, `tests/test_secrets.py` | Green |

## 2. What `login` and `doctor --write` do (plain language)

**`apple-music-mcp login`**
1. Prints how to copy the `media-user-token` cookie from Safari/Chrome after you sign in to music.apple.com, then asks you to paste it (hidden input). `--browser` instead opens a Chromium window (Playwright) and reads the cookie after you sign in.
2. Downloads the public music.apple.com page, finds its JavaScript bundle and extracts the **developer token** (a JWT the web player itself uses).
3. Calls `GET /v1/me/storefront` once to check that Apple accepts the token, and learns your country (for example `br`).
4. Only if that succeeds does it save the tokens to `~/.config/apple-music-mcp/credentials.json` (permissions 600). **Nothing is written to your Apple account.**

**`apple-music-mcp doctor --write`** (read-only without `--write`)
1. Read checks: developer token valid, one catalog search, one iTunes Search query, one BPM lookup, your storefront, the number of playlists in your library.
2. With `--write` only: it **creates one playlist** named `apple-music-mcp doctor test` with 2 songs, **reorders** them (tests in-place editing), reads it back, then **deletes** it. If Apple refuses the delete, it tells you to delete it in the Music app. That one playlist is the only change to your account.
3. About 10 requests in total, a few seconds.

## 3. Probably broken or unverified

| # | Item | Evidence | Action |
|---|---|---|---|
| 1 | `recent/played/tracks` with `limit=30` | Community reports: values above 10 return an error; history ~30–50 items | Phase 1: use `limit=10` + offsets; verify live |
| 2 | Developer-token regex on the web bundle | Gist-based, layout can change | `doctor` step 1 |
| 3 | Removing a track from a playlist (2 guessed shapes) | Two open-source clients disagree | `doctor --write` reports the working shape |
| 4 | `PATCH` rename, `DELETE` playlist | Seen in one open-source client | `doctor --write` |
| 5 | `/v1/me/ratings/songs?ids=` and 404-means-none | Official endpoint family; 404 semantics assumed | Phase 1 live check |
| 6 | `similar-artists` artist view | Not confirmed in docs we could read | Phase 1 live check, fallback exists |
| 7 | AppleScript `favorited`/`loved`, `«class isot»` date cast | Varies by macOS version | Phase 1 live check on the Mac |
| 8 | GetSongBPM / Deezer response shapes | Docs not reachable from the sandbox | `doctor` BPM step |
| 9 | Whether radio plays show up in recently played (needed for §7.4 "known") | Apple docs ambiguous; one report shows station containers | Phase 1 experiment |

## 4. Divergences from the decision guide (`Apple Music MCP — Guia de decisões` v1.0)

| Guide | Current code | Gap |
|---|---|---|
| §4 SQLite + repository layer + migrations | JSON files (`credentials.json`, `state.json`) | New persistence layer |
| §5 token in macOS Keychain; non-secret config in `.env` | Token in a 0600 JSON file; env vars partially | Move to `keyring`; add `.env` loading |
| §4.1 rate limit ~5 req/s, 2–3 concurrent; retry 1→2→4 s; caches (7 d search, 24 h library) | Retry with jitter only; no limiter; only taste (6 h) and BPM caches | New limiter + caches |
| §6 healthchecks, ntfy, daily doctor, pause mode, iCloud backup, structured logs | None | New |
| §7.1 always ask edit vs create; managed playlists persist purpose/profile/mix | Not persisted | New (needs DB) |
| §7.3 + "foco"; sub-profiles (owner: profile families with sub-categories) | 8 flat profiles | Redesign as families |
| §7.4 known = loved or ≥ 10 plays; level 2 = % known *within* known artists; 1–9 plays excluded | PR #31: known = in library or recently played; known-artists % is a share of the total | Rework in Phase 2 |
| §7.6 preview by default | Skill creates directly unless asked | Skill change |
| §7.8 "não gostei" asks the reason, stored; never bans the artist | Dislikes only flagged | Feedback table |
| §7.9 explicit blacklist | None | New |
| §7.10 refresh opt-in per playlist, Mon 09:00 America/Sao_Paulo, proposal + ntfy, apply after approval, `REFRESH_AUTO_APPLY=false` | Refresh for any playlist, launchd Mon 10:07, plan stored in JSON | Rework |
| §7.11 weekly "Descobertas da semana" ~30 tracks, promotion ≤ 20 % of main (max 30) | Staging size = rotation size (≥ 5), no cap | Rework; owner wants Claude (AI) to curate it, see risk/plan |
| §7.12 names/descriptions in English | Skill lets Claude choose (often pt) | Skill change |
| §8 full history, daily stats snapshots | None | New |
| §11 mypy + ruff on all modules, release-please, Conventional Commits | ruff only; no mypy; no release-please; commits not conventional | New, refactor old code |
| Owner: all docs/PRs/code in English, README in pt-BR **and** English | README in English, a pt-BR quickstart section | Split README |

## 5. Technical debt

1. 25 MCP tools is a wide surface. Some overlap (`reorder_playlist`/`sync_playlist`, `search_songs`/`resolve_tracks`). Consolidate during Phase 2.
2. Skill instructions live only in `SKILL.md`. They are not served by the MCP server (guide §4 wants them available through Dispatch too).
3. `tests/fake_apple.py` is large and hand-written. The guide asks for **recorded fixtures**: after Phase 0 validation, record real (sanitized) responses and replace hand-written payloads where possible.
4. `RetryPolicy` jitter can sleep up to 10 s per attempt. That can exceed the MCP client's tool timeout. The guide requires bounded waits.
5. No type checking. Some functions use loose `dict[str, Any]` payloads.
6. The `state.json` taste cache can grow to hundreds of KB (library songs). This should move to SQLite.

## 6. PRs #31 and #33 against the guide

- **#33** (Apple-only, pending playlists): aligned with the guide (§1, §2). **Merged 2026-11-05.**
- **#31** (familiarity mix): the structure is reusable (bucket model, mix check, `known_tracks`, `deep_cuts`), but the *definitions* diverge (§4 above). **Merged 2026-11-05** by owner decision. The definitions will be reworked in Phase 2 (known = loved or ≥ `KNOWN_MIN_PLAYS` plays; level 2 = % known within known artists).

## 7. Validation log (to be filled during §3.3)

Run on the owner's Mac, account storefront `br`. Tokens are not recorded.

| Date | Step | Result | Detail / fix |
|---|---|---|---|
| 2026-11-05 | `login` (first try) | **FAIL** | `index~<hash>.js: no valid JWT`. Root cause: the bundle holds 3 JWTs with `typ`-first headers, and one is pinned to another origin (401). Fixed in #38: any JWT form, prefer `iss=AMPWebPlay`, crawl chunks, resolve bare `assets/` refs |
| 2026-11-05 | BPM (Deezer) | **FAIL → fixed** | Advanced `artist:"…" track:"…"` query returns nothing live. Fixed in #39 (plain query, fuzzy match) |
| 2026-11-05 | `login` | ok | Signed in, storefront `br` |
| 2026-11-05 | `doctor` (read) | ok | Developer token valid 57 more days (268 chars); catalog search ok (`apple-music`); iTunes fallback ok; BPM via Deezer ok (126 BPM); user token ok; library: 12 playlists |
| 2026-11-05 | `doctor --write` | ok | Created a 2-track test playlist, reordered **in place** (verified), deleted it |
| 2026-11-05 | `doctor`: recently played | ok | 50 songs, 0 non-song items (stations); confirms the ~50-item history |
| 2026-11-05 | `doctor`: song ratings | ok | 24 library songs checked: 1 loved, 0 disliked |
| 2026-11-05 | `doctor`: heavy rotation | ok | 7 items |
| 2026-11-05 | `doctor`: similar artists | ok | 5 artists returned |
| 2026-11-05 | `doctor`: Music app stats (AppleScript) | **warn → fix** | Automation permission granted (error -1728, not -1743). The check used the first API playlist by name, and two such playlists were not among the Music app's user playlists. The Music app also keeps a trailing space in one name ("Epic songs "). Fix: look up the Music app's own spelling (exact, then normalized), skip playlists it lacks. Rerun pending |
| — | Dispatch from iPhone calls `auth_status` | pending | — |
| — | Desktop scheduled task calls an MCP tool | pending | — |

Notes:
- The developer token found live has about 57 days left. `DeveloperTokenProvider` re-scrapes it automatically on expiry or 401.
- The user token's expiry date (cookie *Expires*) is not stored yet. That is a Phase 1 item (guide §5).
