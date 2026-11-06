from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from apple_music_mcp.apple.errors import TokenScrapeError
from apple_music_mcp.apple.tokens import find_bundle_paths, find_jwt, scrape_developer_token

from .fake_apple import FakeAppleMusic


def test_find_bundle_paths_prefers_modern_bundle() -> None:
    html = '<script src="/assets/index-legacy-aa.js"></script><script src="/assets/index-bb.js"></script>'
    assert find_bundle_paths(html) == ["/assets/index-bb.js", "/assets/index-legacy-aa.js"]


def test_find_jwt_skips_expired_and_garbage(jwt_factory: Callable[[float | None], str]) -> None:
    expired = jwt_factory(time.time() - 10)
    valid = jwt_factory(time.time() + 1000)
    js = f'x="eyJhbGciOi.notjwt";y="{expired}";z="{valid}"'
    assert find_jwt(js) == valid
    assert find_jwt("no token here") is None


async def test_scrape_from_fake_web_player(fake_apple: FakeAppleMusic) -> None:
    async with httpx.AsyncClient() as http:
        assert await scrape_developer_token(http) == fake_apple.dev_token


async def test_scrape_reports_clear_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(url__regex=r"https://music\.apple\.com/.*").mock(
        return_value=httpx.Response(200, text="<html>")
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(TokenScrapeError, match="APPLE_MUSIC_DEVELOPER_TOKEN"):
            await scrape_developer_token(http)


def _kid_first_jwt(exp: float) -> str:
    import base64
    import json

    def b64(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    header = b64({"kid": "ABC123", "alg": "ES256", "typ": "JWT"})
    payload = b64({"iss": "X", "exp": int(exp)})
    return f"{header}.{payload}.c2lnbmF0dXJlX2Jsb2I"


def test_find_jwt_accepts_headers_not_starting_with_alg() -> None:
    token = _kid_first_jwt(time.time() + 1000)
    assert not token.startswith("eyJh")  # the old regex required eyJh and missed this
    assert find_jwt(f'const t="{token}";') == token


def test_find_jwt_in_url_encoded_meta_config() -> None:
    import json
    from urllib.parse import quote

    token = _kid_first_jwt(time.time() + 1000)
    config = quote(json.dumps({"MEDIA_API": {"token": token}}))
    html = f'<meta name="desktop-music-app/config/environment" content="{config}">'
    assert find_jwt(html) == token


async def test_scrape_follows_chunks_from_tilde_named_index(respx_mock: respx.MockRouter) -> None:
    """Reproduces the first live failure: index~<hash>.js has no JWT; it lives in a chunk."""
    token = _kid_first_jwt(time.time() + 86400)
    html = '<html><script type="module" src="/assets/index~cc245af156.js"></script></html>'
    index_js = 'import("./musickit~9a8b7c.js");import("./vendor~1111.js");console.log("no token here")'
    respx_mock.get(url__regex=r"https://music\.apple\.com/(us/browse)?$").mock(
        return_value=httpx.Response(200, text=html)
    )
    respx_mock.get("https://music.apple.com/assets/index~cc245af156.js").mock(
        return_value=httpx.Response(200, text=index_js)
    )
    respx_mock.get("https://music.apple.com/assets/musickit~9a8b7c.js").mock(
        return_value=httpx.Response(200, text=f'export const devToken="{token}";')
    )
    vendor = respx_mock.get("https://music.apple.com/assets/vendor~1111.js").mock(
        return_value=httpx.Response(200, text="x")
    )
    async with httpx.AsyncClient() as http:
        assert await scrape_developer_token(http) == token
    assert not vendor.called  # "musickit" chunks are tried before vendor chunks


async def test_scrape_crawl_is_bounded(respx_mock: respx.MockRouter) -> None:
    from apple_music_mcp.apple import tokens

    html = '<script src="/assets/index~0.js"></script>'
    respx_mock.get(url__regex=r"https://music\.apple\.com/(us/browse)?$").mock(
        return_value=httpx.Response(200, text=html)
    )

    def chunk(request: httpx.Request) -> httpx.Response:
        n = int(request.url.path.rsplit("~", 1)[1].split(".")[0])
        return httpx.Response(200, text=f'import("./c~{n + 1}.js");import("./c~{n + 1000}.js")')

    route = respx_mock.get(url__regex=r"https://music\.apple\.com/assets/.*\.js").mock(side_effect=chunk)
    async with httpx.AsyncClient() as http:
        with pytest.raises(TokenScrapeError, match="script"):
            await scrape_developer_token(http)
    assert route.call_count <= 2 * tokens.MAX_SCRIPTS  # two pages tried, each capped


def test_find_script_refs() -> None:
    from apple_music_mcp.apple.tokens import find_script_refs

    text = '<script src="/assets/index~ab.js"></script> import("./x~1.js") "//cdn.example/y.js"'
    assert find_script_refs(text) == ["/assets/index~ab.js", "./x~1.js"]


def _web_player_jwt(iss: str, exp: float, origin: bool = False) -> str:
    """FAKE token shaped like the live web player's: ``typ``-first header, root_https_origin claim."""
    import base64
    import json

    def b64(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")

    payload = {"iss": iss, "iat": int(exp) - 86400, "exp": int(exp), "root_https_origin": ["apple.com"]}
    if origin:
        payload["origin"] = ["https://example.apple.com"]
    header = b64({"typ": "JWT", "alg": "ES256", "kid": "FAKEKID000"})
    return f"{header}.{b64(payload)}.ZmFrZV9zaWduYXR1cmVfbm90X3JlYWw"


def test_find_jwt_prefers_web_player_token_over_origin_restricted() -> None:
    """Live bundle (Nov 2026) holds three tokens; the origin-pinned one is rejected by amp-api (401)."""
    exp = time.time() + 86400
    pinned = _web_player_jwt("M62YD85FTQ", exp, origin=True)
    other = _web_player_jwt("5IKPP2IECQ", exp)
    web = _web_player_jwt("AMPWebPlay", exp)
    assert web.startswith("eyJ0eXAi")
    assert find_jwt(f'a="{pinned}";b="{other}";c="{web}"') == web
    assert find_jwt(f'a="{pinned}";b="{other}"') == other
    assert find_jwt(f'a="{pinned}"') == pinned  # last resort, still better than nothing


async def test_scrape_live_structure_token_in_index_and_bare_asset_refs(respx_mock: respx.MockRouter) -> None:
    """Reproduces music.apple.com: tokens inside index~<hash>.js, preload deps as bare ``assets/x.js``."""
    from apple_music_mcp.apple.tokens import resolve_script_ref

    exp = time.time() + 86400
    web = _web_player_jwt("AMPWebPlay", exp)
    pinned = _web_player_jwt("M62YD85FTQ", exp, origin=True)
    html = '<script type="module" crossorigin src="/assets/index~cc245af156.js"></script>'
    index_js = f'const m=["assets/chunk~1.js"];const a="{pinned}";const b="{web}";'
    respx_mock.get(url__regex=r"https://music\.apple\.com/(us/browse)?$").mock(
        return_value=httpx.Response(200, text=html)
    )
    respx_mock.get("https://music.apple.com/assets/index~cc245af156.js").mock(
        return_value=httpx.Response(200, text=index_js)
    )
    async with httpx.AsyncClient() as http:
        assert await scrape_developer_token(http) == web
    base = "https://music.apple.com/assets/index~cc245af156.js"
    assert resolve_script_ref(base, "assets/chunk~1.js") == "https://music.apple.com/assets/chunk~1.js"
    assert resolve_script_ref(base, "./chunk~2.js") == "https://music.apple.com/assets/chunk~2.js"
