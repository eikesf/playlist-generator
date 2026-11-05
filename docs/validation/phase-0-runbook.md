# Phase 0 — live validation runbook (owner's Mac)

Goal: confirm the code works against the real Apple Music API on the owner's account (guide §3.3) and record the results in `docs/audit.md` §7.

## A. Owner, in the macOS Terminal (once)

```bash
brew install uv                      # if uv is not installed
cd <path-to-your-clone>/playlist-generator
git checkout develop && git pull
uv tool install --force .
apple-music-mcp login                # paste the media-user-token (steps are printed); input is hidden
```
Before pasting, note the cookie's **Expires** date shown next to `media-user-token` in the browser's dev tools. Claude Code will record it.

## B. Prompt to paste into Claude Code (opened in the repository folder)

> Read `CLAUDE.md`, `docs/audit.md` and `docs/validation/phase-0-runbook.md`. We are running the Phase 0 live validation.
> 1. Set up the dev environment and run the test suite and lint (`uv venv && uv pip install -e ".[dev]"`, `uv run pytest -q`, `uv run ruff check .`). Report the results.
> 2. Run `apple-music-mcp status` and `apple-music-mcp doctor` (read-only). Show me the output with anything token-like redacted.
> 3. Ask me before running `apple-music-mcp doctor --write` (it creates, reorders and deletes one test playlist). Run it only after I say yes.
> 4. For every `[FAIL]` or `[warn]`, explain the likely cause by reading the relevant code. Do **not** make extra API calls beyond what is needed to diagnose a single failure.
> 5. Create a branch `docs/phase-0-validation` from `develop`. Fill the validation log table in `docs/audit.md` §7 with the date, the step, the result and the doctor detail (redacted). Add the token expiry date I give you. Commit (Conventional Commits) and open a PR into `develop` using the PR template.
> 6. For each real bug, open a GitHub issue with the bug template. Propose the fix, but don't implement it until I approve.
> Never print or commit secrets. Answer me in Portuguese.

## C. Owner, manual checks (report the results to Claude Code so it records them)

1. **Claude Desktop:** `apple-music-mcp install-claude`, quit Claude Desktop with ⌘Q, reopen it, and confirm the `apple-music` tools appear.
2. **Dispatch (iPhone):** in the Claude app, send "Use the apple-music tools and call auth_status". Expected: the same result as on the Mac.
3. **Desktop scheduled task:** in Claude Desktop, create a one-off scheduled task for 5 minutes from now: "Call auth_status from the apple-music tools and summarize". Expected: it runs on the Mac and can reach the local MCP server.
4. **AppleScript permission:** the first time stats are read, macOS asks to let the app control Music. Allow it. If it was denied: System Settings → Privacy & Security → Automation.

## D. Exit criteria (guide §3.3)
- All required `doctor` checks are `[ok]`. Warnings are understood and either fixed or accepted by the owner.
- `doctor --write` passes and reports the removal request shape.
- Dispatch and the Desktop scheduled task can call the MCP tools.
- Results are recorded in `docs/audit.md` §7 and merged.
