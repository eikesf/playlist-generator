# Phase 0 — Risk assessment of the unofficial Apple Music web API

**Date:** 2026-11-05 · **Decision owner:** Eike · **Status:** awaiting decision (GATE)

## 1. How the code authenticates today

| Piece | Source | Where used |
|---|---|---|
| Developer token (JWT) | Scraped from the public music.apple.com JavaScript bundle (`/assets/index*.js`, first non-expired `eyJh…` JWT). Cached; re-scraped on 401. | `Authorization: Bearer …` on every `amp-api.music.apple.com` call |
| User token | The `media-user-token` cookie of your signed-in music.apple.com session, pasted once (or captured by Playwright with `login --browser`) | `media-user-token` header on `/v1/me/...` calls |
| Identity headers | `Origin`/`Referer: https://music.apple.com`, Safari user agent | All amp-api calls |
| Endpoints | Catalog: `/v1/catalog/{sf}/search`, `/songs`, `/artists/{id}/view/top-songs`, `/artists/{id}/view/similar-artists`. User: `/v1/me/storefront`, `/v1/me/library/playlists` (GET/POST/PATCH/DELETE), `/v1/me/library/playlists/{id}/tracks` (GET/POST/DELETE), `/v1/me/library/songs`, `/v1/me/recent/played/tracks`, `/v1/me/history/heavy-rotation`, `/v1/me/ratings/songs` | — |
| Other services | iTunes Search API (public, no auth); Deezer public API; GetSongBPM (free key). Only artist and title are sent. | Search fallback, BPM |

## 2. What the research found

### 2.1 Terms of use
- The **Apple Media Services Terms** forbid using "any software, device, or automated process, or any similar manual process, to scrape, copy, or monitor Content or Services", and separately forbid manipulating play counts or ratings through bots or scripts ([Apple Media Services Terms](https://www.apple.com/legal/internet-services/itunes/), summary via [terms.law](https://terms.law/ToS-Watchdog/cloud-saas/apple/)).
- The Apple website terms forbid "robot" or "spider" tools on the site ([Apple Website Terms of Use](https://www.apple.com/legal/internet-services/terms/site.html)).
- **Reading:** programmatic use of the web player's API with your own session is an *automated process* using the Services. A strict reading puts this project outside the terms. We don't download audio, don't touch play counts, and don't share content, so we avoid the most serious categories (piracy, manipulation). The clause still covers us. This is not legal advice.

### 2.2 Enforcement evidence
- We found **no public report of an Apple account being banned, suspended or warned** for this kind of use.
- The failures reported by open-source projects that use the same tokens are about **tokens being rejected or revoked**. Music Assistant users see "denied access to me/storefront" when the developer token is invalid, expired or revoked ([music-assistant #6849](https://github.com/music-assistant/support/issues/6849), [#4674](https://github.com/music-assistant/support/issues/4674)). Other projects saw web 401/403 responses after expiry or a lapsed subscription ([LyricsPlus #3](https://github.com/ibratabian17/LyricsPlus/issues/3), [music-posters #60](https://github.com/enrique7mc/music-posters/issues/60)).
- The scraping gist itself warns the method "is not sanctioned by Apple and is subject to break without notice" ([gist](https://gist.github.com/sayeed205/813a3ae2c163e0a661b7b28038b444ba)).

### 2.3 Token lifetime
- An Apple developer-forum moderator states the Music User Token currently expires after **6 months** and the lifetime is not configurable ([Apple forums 654814](https://developer.apple.com/forums/thread/654814)). Music Assistant documents re-authentication after **180 days** and suggests reading the cookie's *Expires* date in the browser ([Music Assistant docs](https://www.music-assistant.io/music-providers/apple-music/)). Some reports of 1–2 day tokens refer to other flows.
- **Implication:** expiry is predictable. `login` should store the cookie's expiry date (pasted by the user or captured by `--browser`) and the ntfy reminder should fire a few days before.

### 2.4 Recently played (relevant to "known" tracks heard on radio)
- Community reports: `limit` above 10 errors, and the history covers only the last ~30–50 items. The endpoint may also return station containers ([Apple forums 669720](https://developer.apple.com/forums/thread/669720), [735988](https://developer.apple.com/forums/thread/735988), [Cory Dransfeldt](https://www.coryd.dev/posts/2023/displaying-listening-data-from-apple-music-using-musickitjs), [DEV post](https://dev.to/jurooravec/failed-trying-to-get-recently-played-songs-from-apple-music-api-44im)).
- Polling it would also be "monitoring" in the literal sense of §2.1. See the mitigations below.

### 2.5 Claude Dispatch and scheduled tasks (for running on the Mac)
- Secondary sources say Dispatch sends tasks from the phone to the Claude Desktop app on your Mac and uses its local files and connectors. It stops if the Mac sleeps or the app closes. **Desktop scheduled tasks run on the Mac** (with a keep-awake option), while cloud scheduled tasks cannot reach local MCP servers ([DataCamp](https://www.datacamp.com/tutorial/claude-cowork-dispatch), [buildtolaunch](https://buildtolaunch.substack.com/p/claude-cowork-scheduled-tasks-vs-routines-vs-loop), [lowcode.agency](https://www.lowcode.agency/blog/claude-dispatch-explained)). **[VERIFY]** against Anthropic's docs and with a live test (audit §7).
- **Implication:** a Desktop scheduled task can let **Claude itself** curate "Descobertas da semana" every Monday at no extra cost. That meets the owner's request that the AI create it.

## 3. Alternatives that cost nothing

| Option | What it can do | Limits | Risk to the account |
|---|---|---|---|
| **A. Web-player API (current)** | Everything: catalog, library, playlists, ratings | Can break when Apple changes the web player | Outside the terms (strict reading); no enforcement reports |
| **B. AppleScript / Music app only** | Create playlists; add tracks **already in your library**; read plays, skips and favorites | Cannot search the Apple Music catalog. Adding catalog songs not in the library is unreliable: Apple acknowledged a bug, and radio tracks are not added ([Apple forums 694200](https://developer.apple.com/forums/thread/694200), [MacScripter](https://www.macscripter.net/t/how-to-add-a-track-to-library-in-apple-music-using-apple-script/72896)). Discovery is impossible. | Lowest. It is Apple's own scripting interface on your Mac, though still an "automated process" |
| **C. Shortcuts app** | Similar to B | "Add to Playlist" is reported as unreliable on macOS ([Apple forums 694200](https://developer.apple.com/forums/thread/694200)) | Low |
| **D. UI scripting the Music app (simulated clicks)** | Could add catalog songs | Very fragile, needs Accessibility permission, slow | Same clause as A |
| **E. Official MusicKit / Apple Music API** | Everything, sanctioned | Needs the paid Apple Developer Program. Free accounts can't enable MusicKit ([Apple forums 706861](https://developer.apple.com/forums/thread/706861)) | None (excluded by owner: cost) |
| **F. Hybrid (recommended)** | Catalog search through the **public iTunes Search API**; listening stats through **AppleScript** (local, no network); web-player API **only for writes and the few user reads that have no other source** (create/edit playlists, ratings, library) | Still depends on A for writes | Exposure to A drops to a handful of calls per session or week |

## 4. Classification

| Dimension | Level | Why |
|---|---|---|
| **Technical break** (stops working) | **Medium–High** | Undocumented API, scraped developer token, documented revocations in other projects. Mitigated by `doctor` daily alerts and a swappable client layer. |
| **Account** (ban or warning) | **Low–Medium** | The terms clause is explicit, so it cannot be called "low" with confidence. But we found no reported enforcement, and our use is low-volume, personal and non-extractive. The likely failure mode is the token being rejected, not the account being sanctioned. |

Under guide §3.2 ("if the account risk is medium or high, stop and present the alternatives"), this sits on the boundary. It is presented to the owner for a decision.

## 5. Mitigations (apply under A or F)

1. **Minimize the footprint.** Use option F: iTunes Search for catalog reads, AppleScript for stats, the web API only for writes and unavoidable user reads.
2. **Low, human-like volume.** ≤ 5 req/s with 2–3 concurrent at most (guide §4.1), but typical sessions stay well below that: tens of calls per session. No parallel fan-out over hundreds of tracks. Caches for search (7 d), library (24 h) and BPM (permanent).
3. **No continuous monitoring.** Don't poll recently played every few minutes. If we use it for "heard on radio" detection, do it at most a few times a day, alongside the daily snapshot. AppleScript covers library tracks with zero network calls.
4. **Never touch counts or ratings.** No writes to ratings and no plays triggered by the tool. Reading only.
5. **Behave like the web player.** Same headers, honor `Retry-After`, back off hard on 401/403/429, and stop all scheduled jobs after an auth failure until `login`.
6. **Single user, own session.** No sharing of tokens or results, no public service (the guide already excludes hosting).
7. **Daily `doctor`** detects breakage early, and the expiry date stored at `login` drives the reminders.
8. **Kill switch.** One config flag (`APPLE_WEB_API_ENABLED=false`) falls back to read-only AppleScript features if the owner ever wants to stop using the web API.

## 6. Recommendation

**Proceed with mitigations, using option F (hybrid).** No free option can search the catalog and add new songs without the web-player API. A or F is required for the product the owner wants. F cuts the exposure to the smallest practical surface. If the owner considers the terms clause unacceptable, the only zero-cost fallback is **B**, which means a library-only tool with no discovery.

**Decision needed from the owner (GATE):** F with mitigations / A as-is / B only.
