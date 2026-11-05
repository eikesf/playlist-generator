from __future__ import annotations

import time

import pytest

from apple_music_mcp.apple.errors import AuthError
from apple_music_mcp.apple.http import RetryPolicy
from apple_music_mcp.config import Credentials, load_credentials

from .conftest import build_app


async def test_search_scrapes_token_and_persists_it(app, fake_apple):
    songs = await app.catalog.search_songs("Charlotte de Witte Doppler", limit=5)
    assert {s.name for s in songs} >= {"Doppler", "Doppler (Radio Edit)"}
    assert songs[0].source == "apple-music"
    assert load_credentials().developer_token == fake_apple.dev_token


async def test_search_limit_is_capped_to_api_maximum(app, fake_apple):
    await app.catalog.search_songs("Charlotte", limit=500)  # fake API returns 400 above 25
    assert fake_apple.calls[-1][1].endswith("/search")
    assert all(s.source == "apple-music" for s in await app.catalog.search_songs("Charlotte", limit=500))


async def test_rotated_developer_token_is_refreshed_once(app, fake_apple, jwt_factory):
    app.credentials.developer_token = jwt_factory(time.time() + 86400)  # valid JWT, but stale for Apple
    songs = await app.catalog.search_songs("Amelie Lens Hypnotized")
    assert songs and songs[0].name == "Hypnotized"
    assert app.credentials.developer_token == fake_apple.dev_token


async def test_retries_on_rate_limit(app, fake_apple):
    fake_apple.fail_next = [429, 503]
    songs = await app.catalog.search_songs("Stephan Bodzin Singularity")
    assert songs[0].id == "4001"
    assert len([c for c in fake_apple.calls if c[1].endswith("/search")]) == 3


def test_retry_policy_delay_honours_retry_after():
    import httpx

    policy = RetryPolicy(max_delay=5)
    assert policy.delay(httpx.Response(429, headers={"Retry-After": "2"}), 1) == 2
    assert policy.delay(httpx.Response(429, headers={"Retry-After": "60"}), 1) == 5
    assert 0 <= policy.delay(httpx.Response(503), 3) <= 5


async def test_falls_back_to_itunes_search_when_amp_unavailable(app, fake_apple):
    fake_apple.amp_available = False
    songs = await app.catalog.search_songs("Boris Brejcha Gravity")
    assert songs[0].id == "5001"
    assert songs[0].source == "itunes-search"


async def test_artist_lookup_and_top_songs(app):
    artist = await app.catalog.find_artist("amelie lens")
    assert artist and artist.name == "Amelie Lens"
    top = await app.catalog.artist_top_songs(artist.id, limit=2)
    assert [s.name for s in top] == ["Hypnotized", "Feel It"]
    found, songs = await app.catalog.top_songs_for("Eric Prydz")  # no artist page in the fake
    assert found is None and [s.name for s in songs] == ["Opus"]


async def test_get_songs_by_id(app):
    songs = await app.catalog.get_songs(["4002", "1001", "999999"])
    assert [s.id for s in songs] == ["4002", "1001"]


async def test_user_storefront_requires_user_token(fake_apple):
    async with build_app(Credentials(storefront="br"), persist=False) as a:
        with pytest.raises(AuthError, match="apple-music-mcp login"):
            await a.library.storefront()
    async with build_app(Credentials(media_user_token="wrong-token-xxxxxxxx"), persist=False) as a:
        with pytest.raises(AuthError) as err:
            await a.library.storefront()
        assert err.value.status == 403


async def test_user_storefront_ok(app):
    assert await app.library.storefront() == "br"


async def test_persist_false_never_writes_credentials(fake_apple):
    async with build_app(Credentials(media_user_token=fake_apple.user_token), persist=False) as a:
        await a.catalog.search_songs("Opus")
    assert load_credentials().developer_token is None
