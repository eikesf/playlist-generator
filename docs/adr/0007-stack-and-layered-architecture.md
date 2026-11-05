# 0007. Stack and layered architecture

**Status:** Accepted

## Context
The project should be easy to maintain, test and extend, and read as market-standard code for a portfolio.

## Decision
**Stack**
- **Python 3.11+**, the owner's main language, with native asyncio.
- **MCP Python SDK 2.x (`MCPServer`)**, the official SDK. stdio transport is what Claude Desktop and Claude Code launch.
- **httpx (async)**, an HTTP client with a pluggable transport, which makes respx-based tests possible.
- **rapidfuzz**: fast, MIT-licensed fuzzy matching for track resolution.
- **pydantic** (from the MCP SDK) for typed tool inputs (`TrackInput`).
- **uv** (install), **ruff** (lint/format), **pytest + pytest-asyncio + respx** (tests), **GitHub Actions** (CI matrix: Ubuntu/macOS × Python 3.11/3.13), **hatchling** (packaging; the skill ships inside the wheel).

**Architecture (SOLID/DRY)**
- `apple/` is the integration layer: `AmpTransport` (headers, auth, retry, token rotation, pagination), `DeveloperTokenProvider`, `CatalogService`, `LibraryService`, `ITunesSearch`, one `RetryPolicy`, and an error hierarchy.
- `resolver.py`, `curation/` and `editing.py` are domain logic. They depend on protocols (`ports.SongSearcher`) and services, not on HTTP.
- `container.py` is the composition root, the only place objects are wired. Tests inject fake HTTP, no-op sleep and temporary stores.
- `tools/` holds the MCP adapters, one module per domain, behind a single `@tool_errors` decorator.
- `commands/` holds the CLI adapters (`login`, `doctor` as a list of `Check` objects, `install`).

## Consequences
- Each module has one reason to change, and new profiles, checks, tools or edit strategies are additions rather than edits.
- There are more files than a single script, but each one is small (under 300 lines) and focused.
