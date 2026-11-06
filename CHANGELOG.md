# Changelog

## [0.2.0](https://github.com/eikesf/playlist-generator/compare/apple-music-mcp-v0.1.0...apple-music-mcp-v0.2.0) (2026-11-06)


### Features

* **backup:** daily verified database backup to iCloud Drive with retention ([#59](https://github.com/eikesf/playlist-generator/issues/59)) ([efd3a01](https://github.com/eikesf/playlist-generator/commit/efd3a0189242b236475f0479bdb1e9e3c9fb07b1))
* **config:** keep secrets in the macOS Keychain and settings in .env ([#56](https://github.com/eikesf/playlist-generator/issues/56)) ([1a2501b](https://github.com/eikesf/playlist-generator/commit/1a2501b75be67c8ada0307ae4922ec1dff0f4ef9))
* **db:** add SQLite schema, Alembic migrations and settings store ([#55](https://github.com/eikesf/playlist-generator/issues/55)) ([9878126](https://github.com/eikesf/playlist-generator/commit/9878126baa7e9c14ef036b2f6a4f0081f4abe73e)), closes [#52](https://github.com/eikesf/playlist-generator/issues/52)
* **feedback:** "não gostei" with reason and an explicit blacklist ([#69](https://github.com/eikesf/playlist-generator/issues/69)) ([adce2e9](https://github.com/eikesf/playlist-generator/commit/adce2e9dcacd2cf7ed264daa3fa2b346eadf97d2))
* **http:** rate limiter, bounded retry and response cache ([#57](https://github.com/eikesf/playlist-generator/issues/57)) ([deed633](https://github.com/eikesf/playlist-generator/commit/deed633820831fdf30d06c0df349f59147d3481a))
* **jobs:** daily snapshots, recently played collector, daily doctor, launchd ([#58](https://github.com/eikesf/playlist-generator/issues/58)) ([d29003e](https://github.com/eikesf/playlist-generator/commit/d29003ec06c7e57d19515d586d145ce5cad20ab1))
* **mix:** known tracks by the guide's definitions and a two-level mix ([#67](https://github.com/eikesf/playlist-generator/issues/67)) ([54b688c](https://github.com/eikesf/playlist-generator/commit/54b688cd5aa514d34a01e254dd779ff6dc90f2ed))
* **monitoring:** ntfy alerts, healthchecks heartbeat, pause mode, token reminders, logs ([#60](https://github.com/eikesf/playlist-generator/issues/60)) ([7fd84a2](https://github.com/eikesf/playlist-generator/commit/7fd84a266000bc1477837649fef6754461fc3b44))
* **profiles:** profile families, sub-profiles and profiles created in chat ([#66](https://github.com/eikesf/playlist-generator/issues/66)) ([4167908](https://github.com/eikesf/playlist-generator/commit/416790821ff44d268f8132f8282d24d266928bc1))
* **sessions:** edit-or-create sessions, preview before creating, managed playlists, history ([#68](https://github.com/eikesf/playlist-generator/issues/68)) ([88775f5](https://github.com/eikesf/playlist-generator/commit/88775f55876daa4f80e4872988f3bd0e6c527056))
* **skill:** session flow in the skill, served by the MCP server ([7e237cc](https://github.com/eikesf/playlist-generator/commit/7e237cc03abf91e407ae499e0a920a3f3c9a8282))


### Bug Fixes

* find the developer token in any JWT form and in lazy-loaded web player chunks ([cb982d0](https://github.com/eikesf/playlist-generator/commit/cb982d01f6e7b6eb46a24daa8caa132f225ce0d6))
* find the developer token in any JWT form and in lazy-loaded web player chunks ([b551fba](https://github.com/eikesf/playlist-generator/commit/b551fbad6c85343fa979c924ae9d155b0fd8c544))
* **music-app:** match playlists by the Music app's own name ([#44](https://github.com/eikesf/playlist-generator/issues/44)) ([dabe179](https://github.com/eikesf/playlist-generator/commit/dabe179463c04490d822dcd51fa1870d16e82c7f))
* page recently played by 10 and validate unverified endpoints in doctor ([55d4834](https://github.com/eikesf/playlist-generator/commit/55d4834b23ced2eb9babc60efa0c72713ef055d1))
* page recently played by 10 and validate unverified endpoints in doctor ([ae5ce09](https://github.com/eikesf/playlist-generator/commit/ae5ce093ff896e849000c55a4b3043736bb531c3))
* pick the web player's own developer token and resolve bare asset refs ([d3c2d59](https://github.com/eikesf/playlist-generator/commit/d3c2d59fc343458f44dbf63af24f91bcdc93962a))
* **tools:** explain token renewal and BPM sources in auth_status ([#46](https://github.com/eikesf/playlist-generator/issues/46)) ([7cc15b5](https://github.com/eikesf/playlist-generator/commit/7cc15b5a28222f9f6fe7802af2518195d8fc5e80))
* use a plain Deezer search query so BPM lookups find tracks ([1cb0530](https://github.com/eikesf/playlist-generator/commit/1cb0530442112ec2e4f5d17ea18b6f0910164a22))
* use a plain Deezer search query so BPM lookups find tracks ([0e3c900](https://github.com/eikesf/playlist-generator/commit/0e3c900e8fa086a3a36f91460cf8d1b741fdf07b))


### Documentation

* add CLAUDE.md, decision guide and phase 0 validation runbook; run CI on develop ([6b49773](https://github.com/eikesf/playlist-generator/commit/6b49773272e31cf644e67c46c481646b40a8c8b2))
* add phase 0 code audit and API risk assessment ([3323d37](https://github.com/eikesf/playlist-generator/commit/3323d3771608d70d538255946334e0371a621f44))
* phase 0 audit, risk assessment, decisions and validation runbook ([85ef8c9](https://github.com/eikesf/playlist-generator/commit/85ef8c9b058441c1bfdfdc4b898315f913c357fa))
* phase 1 runbook and README sections; fix(logs): readable paths in redaction ([#62](https://github.com/eikesf/playlist-generator/issues/62)) ([b009550](https://github.com/eikesf/playlist-generator/commit/b009550c2b3ff55d602a28f6da0621eb1a9ec191))
* propose phase 1 data model (ERD) for approval ([#53](https://github.com/eikesf/playlist-generator/issues/53)) ([7ad811b](https://github.com/eikesf/playlist-generator/commit/7ad811b0611aa975f4e85d38999de47b5ee655b2))
* record Dispatch and scheduled task validation, close phase 0 checks ([#47](https://github.com/eikesf/playlist-generator/issues/47)) ([7ee0a93](https://github.com/eikesf/playlist-generator/commit/7ee0a93dc9dac8f741c79df094d4c4db6871558b))
* record phase 0 decisions (hybrid approach, polling windows, stack) ([d2bb7f7](https://github.com/eikesf/playlist-generator/commit/d2bb7f7ef7741a3fb5b3fe977ccd58cebc164a79))
* record phase 0 live validation results ([#43](https://github.com/eikesf/playlist-generator/issues/43)) ([f489b1c](https://github.com/eikesf/playlist-generator/commit/f489b1c38a611b1b25cfe141beeb6814becde45b))
* split the README into English and pt-BR ([7bf02ff](https://github.com/eikesf/playlist-generator/commit/7bf02ffa98ffb0d2f656d289cb1fadd3665d1786))
* split the README into English and pt-BR ([f31a599](https://github.com/eikesf/playlist-generator/commit/f31a59936effe8e2784bbc61fe6900222308e229))
