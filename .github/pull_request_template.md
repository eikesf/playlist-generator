<!-- Title must follow Conventional Commits: feat:, fix:, docs:, chore:, refactor:, test:, ci:, perf: -->

## Summary
<!-- What changes and why, in 2–4 sentences. -->

Closes #

## Guide / ADR references
<!-- e.g. Guide §7.10, ADR 0005 -->

## Changes
-

## Tests
<!-- Commands run and results, e.g. `uv run pytest -q` → N passed; `ruff check .`; `mypy` -->

## Risk
- [ ] Touches the Apple web API (if yes: request volume and mitigations considered, see `docs/risk-assessment.md`)
- [ ] Changes the database schema (if yes: Alembic migration included)
- [ ] No secrets, tokens, cookies or personal data in code, tests, fixtures or logs

## GATE
- [ ] This PR is a GATE item and needs the owner's approval before merging
- [ ] Not a GATE item: may be merged automatically once CI is green

## Live verification
<!-- What the owner should run on the Mac (e.g. `apple-music-mcp doctor`), or "none". -->
