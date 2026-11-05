# 0009. Small stacked pull requests, one issue each

**Status:** Accepted

## Context
The project was built autonomously overnight. The owner wanted the work organized as issues, branches and PRs so that it can be reviewed step by step.

## Decision
- One epic (#1) plus one issue per milestone. Each issue gets one branch and one PR. Every PR is based on the previous one (a stack), so each diff shows only its own step.
- CI runs on every PR. Each PR description lists what changed and the test results.
- PRs are merged in order (#6 → #7 → #8 → #9 → #15 → #16 → #17 → #18 → #19). After a PR is merged, GitHub retargets the next one to `main`.

## Consequences
- Review is incremental and each step has green CI.
- `main` only receives the code when the owner merges. The newest branch always contains everything.
