# 0006. Test against a stateful fake Apple API; verify live with `doctor`

**Status:** Accepted

## Context
The project was built in a cloud sandbox whose network policy blocks Apple domains, and the real API needs the owner's personal token. Mocking each call separately would only test the mocks.

## Decision
- `tests/fake_apple.py` is an in-memory imitation of the web player HTML and bundle, amp-api (catalog, `/v1/me`, library playlists and tracks) and iTunes Search, mounted with respx. It keeps state (playlists, tracks) and enforces Apple-like rules: 401 for a bad developer token, 403 for a bad user token, 400 for a limit above 25 or more than 100 tracks per add. Switches simulate unsupported edit endpoints and midway failures.
- Tests run at every level: unit (resolver, profiles, sequencer, config), service (catalog, library, editor), MCP protocol (in-process client and a **real stdio subprocess** with the legacy `initialize` handshake), and end-to-end simulated sessions (plan → resolve → order → create → read back).
- `apple-music-mcp doctor [--write]` runs the same steps against the real endpoints on the user's Mac.

## Consequences
- More than 100 tests run offline in about 4 seconds, in CI on Ubuntu and macOS.
- The fake encodes our understanding of the API, so a wrong assumption would pass both the code and the tests. `doctor` is the safety net, and it must be run after setup and whenever something looks off.
