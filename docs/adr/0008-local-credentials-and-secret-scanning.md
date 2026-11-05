# 0008. Credentials stay local; a secret scan guards the repository

**Status:** Accepted

## Context
The `media-user-token` gives access to the owner's Apple Music library, and the repository is meant to be shared.

## Decision
- Credentials live only in `~/.config/apple-music-mcp/credentials.json`: directory 0700, file 0600, atomic writes. They are sent only to Apple. Tool output and logs show masked values (`abc123...wxyz (180 chars)`).
- Environment variables (`APPLE_MUSIC_USER_TOKEN`, ...) are supported, and `.env.example` documents them empty.
- `.gitignore` blocks `.env*` (except the example), credential files, the Playwright browser profile (it holds session cookies), keys and certificates, and Claude Desktop config copies.
- `tests/test_secrets.py` runs in CI. It fails if a tracked file looks like a secret file or contains a JWT, a private key block, a `media-user-token` value or a filled token variable. It also asserts that `.gitignore` really blocks those paths. An optional pre-commit config adds gitleaks.

## Consequences
- An accidental commit of a credential fails CI before it can be merged.
- Test fixtures generate JWTs at runtime, so they never trip the scanner.
