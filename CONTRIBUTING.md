# Contributing

- **Language:** code, identifiers, commits, issues, PRs, ADRs and docs are in English. The only exception is `README.pt-BR.md`.
- **Flow:** issue (use a template) → branch from `develop` → PR into `develop` (template) → CI green → merge (branch is deleted automatically). Releases: PR `develop` → `main`, approved by the owner. GATE items from the decision guide also need the owner's approval.
- **Protected branches:** `main` and `develop` only accept changes through PRs with green CI; no direct pushes, force pushes or deletion. PRs into `main` must come from `develop` (releases) or `hotfix/…` (emergency fixes); the `Main source branch` workflow enforces this. A hotfix merged into `main` is merged back into `develop` right after.
- **Commits and PR titles:** [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `ci:`, `perf:`). Releases are generated from them.
- **Quality gates:** `ruff check .`, `ruff format --check .`, `mypy` and `pytest` must pass. CI runs without credentials.
- **Secrets:** never commit tokens, cookies, `.env` files or ntfy topics. `tests/test_secrets.py` enforces this.
- **Branches:** `feat/…`, `fix/…`, `docs/…`, `chore/…`, `refactor/…`, `ci/…`, `hotfix/…` (from `main`, only for urgent fixes).
- **Releases and tags:** each release is a [SemVer](https://semver.org/) tag `vMAJOR.MINOR.PATCH` created on `main` with a GitHub release. Tags matching `v*` cannot be deleted or moved.
