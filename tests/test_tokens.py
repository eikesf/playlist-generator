from __future__ import annotations

import time

import httpx
import pytest

from apple_music_mcp.tokens import TokenScrapeError, find_bundle_paths, find_jwt, scrape_developer_token


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
    with pytest.raises(TokenScrapeError, match="APPLE_MUSIC_DEVELOPER_TOKEN"):
        await scrape_developer_token()
