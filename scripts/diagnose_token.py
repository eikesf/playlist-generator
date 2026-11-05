"""Diagnose developer-token discovery against the live music.apple.com web player.

Fetches the browse page, lists every referenced script, fetches them (bounded, same origin) and
reports JWT-like matches and hints that the token is built at runtime. It never prints a full
token: only its first 8 characters, its length, the decoded header/payload keys, ``iss`` and ``exp``.

    uv run python scripts/diagnose_token.py [--max-assets 60]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
from datetime import UTC, datetime
from urllib.parse import unquote

import httpx

from apple_music_mcp.apple.http import BROWSER_UA
from apple_music_mcp.apple.tokens import find_jwt, find_script_refs, resolve_script_ref

PAGE_URL = "https://music.apple.com/us/browse"
ORIGIN = "https://music.apple.com/"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
RUNTIME_HINTS = {
    "jwt header fragment (eyJ)": re.compile(r"""["'`]eyJ[A-Za-z0-9_-]{0,9}["'`]"""),
    "atob / base64 decode": re.compile(r"\batob\s*\("),
    "developerToken identifier": re.compile(r"developerToken|developer_token|devToken", re.I),
    "Authorization Bearer": re.compile(r"""Bearer\s*["'`]?\s*\+|`Bearer \$\{|["']Bearer ["']"""),
    "token endpoint / config fetch": re.compile(
        r"""["'`][^"'`]*(?:token|config|environment)[^"'`]*\.json""", re.I
    ),
    "meta environment config": re.compile(r"config/environment"),
    "MEDIA_API / amp-api": re.compile(r"MEDIA_API|amp-api\.music\.apple\.com"),
}


def mask(token: str) -> str:
    return f"{token[:8]}... ({len(token)} chars)"


def decode_part(part: str) -> dict | None:
    try:
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def describe_jwt(token: str) -> str:
    header, payload = (decode_part(p) for p in token.split(".")[:2])
    if header is None or payload is None:
        return f"{mask(token)} (not decodable)"
    exp = payload.get("exp")
    exp_s = datetime.fromtimestamp(exp, UTC).isoformat() if isinstance(exp, int | float) else "none"
    return (
        f"{mask(token)} header_keys={sorted(header)} payload_keys={sorted(payload)} "
        f"iss={payload.get('iss')!r} exp={exp_s}"
    )


def report(label: str, text: str) -> None:
    print(f"\n== {label}  ({len(text):,} bytes)")
    for source, body in (("raw", text), ("url-decoded", unquote(text))):
        if source == "url-decoded" and "%22" not in text:
            continue
        for token in dict.fromkeys(m.group(0) for m in JWT_RE.finditer(body)):
            print(f"  JWT [{source}]: {describe_jwt(token)}")
    chosen = find_jwt(text)
    if chosen:
        print(f"  scraper would pick: {describe_jwt(chosen)}")
    for name, rx in RUNTIME_HINTS.items():
        m = rx.search(text)
        if m:
            hits = rx.findall(text)
            # Show short context snippets with any JWT-looking run redacted.
            start = max(0, m.start() - 60)
            snippet = JWT_RE.sub(lambda t: mask(t.group(0)), text[start : m.end() + 60])
            snippet = re.sub(r"[A-Za-z0-9_-]{40,}", lambda t: mask(t.group(0)), snippet)
            print(f"  hint: {name} x{len(hits)}  …{snippet!r}…")


async def main(max_assets: int) -> None:
    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, headers={"User-Agent": BROWSER_UA}
    ) as http:
        page = await http.get(PAGE_URL, headers={"Accept": "text/html"})
        print(f"page {PAGE_URL}: HTTP {page.status_code}")
        report(PAGE_URL, page.text)
        queue = [resolve_script_ref(PAGE_URL, r) for r in find_script_refs(page.text)]
        print("\nscripts referenced by the page:")
        for url in queue:
            print(f"  {url}")
        visited: list[str] = []
        while queue and len(visited) < max_assets:
            url = queue.pop(0)
            if url in visited or not url.startswith(ORIGIN):
                continue
            visited.append(url)
            resp = await http.get(url)
            if resp.status_code != 200:
                print(f"\n== {url}: HTTP {resp.status_code}")
                continue
            report(url, resp.text)
            queue += [
                u
                for u in (resolve_script_ref(url, r) for r in find_script_refs(resp.text))
                if u not in visited
            ]
        print(f"\nfetched {len(visited)} asset(s); {len(queue)} left in queue (cap {max_assets})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-assets", type=int, default=60)
    asyncio.run(main(parser.parse_args().max_assets))
