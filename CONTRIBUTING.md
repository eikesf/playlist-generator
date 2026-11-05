# Contributing

- **Language:** code, identifiers, commits, issues, PRs, ADRs and docs are in English. The only exception is `README.pt-BR.md`.
- **Flow:** issue (use a template) → branch → PR (template) → CI green → merge. GATE items from the decision guide need the owner's approval.
- **Commits and PR titles:** [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `ci:`, `perf:`). Releases are generated from them.
- **Quality gates:** `ruff check .`, `ruff format --check .`, `mypy` and `pytest` must pass. CI runs without credentials.
- **Secrets:** never commit tokens, cookies, `.env` files or ntfy topics. `tests/test_secrets.py` enforces this.
- **Branches:** `feat/…`, `fix/…`, `docs/…`, `chore/…`, `refactor/…`.
