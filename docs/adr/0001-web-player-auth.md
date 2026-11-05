# 0001. Authenticate with the music.apple.com web-player session

**Status:** Accepted

## Context
Creating a playlist in someone's Apple Music library through the official Apple Music API requires a developer token signed with a MusicKit key. That key needs the Apple Developer Program, which costs about US$99/year, plus a Music User Token obtained through MusicKit JS. This is a personal tool and the owner did not want a yearly fee.

The music.apple.com web player calls the same API family at `amp-api.music.apple.com`. It authenticates with a developer JWT embedded in its public JavaScript bundle and a `media-user-token` cookie for the signed-in user. Several open-source clients document this approach (LunaParker/apple-music-py, Myp3a/apple-music-api).

## Decision
- Scrape the developer JWT from the web player's `/assets/index*.js` bundle. Cache it with its `exp`, and re-scrape on expiry or on a 401 (Apple rotates it).
- The user pastes the `media-user-token` cookie once (`apple-music-mcp login`), or captures it with an optional Playwright login window. It is validated against `/v1/me/storefront` **before** being saved.
- Send the same headers as the web player (`Origin`/`Referer: https://music.apple.com`).

## Consequences
- The tool costs nothing to run.
- The API is **undocumented**. Apple can change it without notice, and using it sits outside the official developer terms. It is fine for personal use, not for a commercial product.
- The user token expires from time to time, so the user has to log in again. Tools report `AUTH: ...` with that instruction.
- `apple-music-mcp doctor` exists to pinpoint which step broke if Apple changes something.
- If Apple changes the bundle layout, a developer token can be set manually via `APPLE_MUSIC_DEVELOPER_TOKEN`.
