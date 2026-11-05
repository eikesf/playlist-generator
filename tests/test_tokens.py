from __future__ import annotations

import time

import httpx
import pytest

from apple_music_mcp.apple.tokens import TokenScrapeError, find_bundle_paths, find_jwt, scrape_developer_token


def test_find_bundle_paths_prefers_modern_bundle():
    html = '<script src="/assets/index-legacy-aa.js"></script><script src="/assets/index-bb.js"></script>'
    assert find_bundle_paths(html) == ["/assets/index-bb.js", "/assets/index-legacy-aa.js"]


def test_find_jwt_skips_expired_and_garbage(jwt_factory):
    expired = jwt_factory(time.time() - 10)
    valid = jwt_factory(time.time() + 1000)
    js = f'x="eyJhbGciOi.notjwt";y="{expired}";z="{valid}"'
    assert find_jwt(js) == valid
    assert find_jwt("no token here") is None


async def test_scrape_from_fake_web_player(fake_apple):
    async with httpx.AsyncClient() as http:
        assert await scrape_developer_token(http) == fake_apple.dev_token


async def test_scrape_reports_clear_error(respx_mock):
    respx_mock.get(url__regex=r"https://music\.apple\.com/.*").mock(
        return_value=httpx.Response(200, text="<html>")
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(TokenScrapeError, match="APPLE_MUSIC_DEVELOPER_TOKEN"):
            await scrape_developer_token(http)


def _kid_first_jwt(exp: float) -> str:
    import base64
    import json

    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    header = b64({"kid": "ABC123", "alg": "ES256", "typ": "JWT"})
    payload = b64({"iss": "X", "exp": int(exp)})
    return f"{header}.{payload}.c2lnbmF0dXJlX2Jsb2I"


def test_find_jwt_accepts_headers_not_starting_with_alg():
    token = _kid_first_jwt(time.time() + 1000)
    assert not token.startswith("eyJh")  # the old regex required eyJh and missed this
    assert find_jwt(f'const t="{token}";') == token


def test_find_jwt_in_url_encoded_meta_config():
    import json
    from urllib.parse import quote

    token = _kid_first_jwt(time.time() + 1000)
    config = quote(json.dumps({"MEDIA_API": {"token": token}}))
    html = f'<meta name="desktop-music-app/config/environment" content="{config}">'
    assert find_jwt(html) == token


async def test_scrape_follows_chunks_from_tilde_named_index(respx_mock):
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


async def test_scrape_crawl_is_bounded(respx_mock):
    from apple_music_mcp.apple import tokens

    html = '<script src="/assets/index~0.js"></script>'
    respx_mock.get(url__regex=r"https://music\.apple\.com/(us/browse)?$").mock(
        return_value=httpx.Response(200, text=html)
    )

    def chunk(request):
        n = int(request.url.path.rsplit("~", 1)[1].split(".")[0])
        return httpx.Response(200, text=f'import("./c~{n + 1}.js");import("./c~{n + 1000}.js")')

    route = respx_mock.get(url__regex=r"https://music\.apple\.com/assets/.*\.js").mock(side_effect=chunk)
    async with httpx.AsyncClient() as http:
        with pytest.raises(TokenScrapeError, match="script"):
            await scrape_developer_token(http)
    assert route.call_count <= 2 * tokens.MAX_SCRIPTS  # two pages tried, each capped


def test_find_script_refs():
    from apple_music_mcp.apple.tokens import find_script_refs

    text = '<script src="/assets/index~ab.js"></script> import("./x~1.js") "//cdn.example/y.js"'
    assert find_script_refs(text) == ["/assets/index~ab.js", "./x~1.js"]
