from __future__ import annotations

import time

import pytest

from apple_music_mcp.client import AppleMusicClient, AuthError
from apple_music_mcp.config import Credentials, load_credentials


async def test_search_scrapes_token_and_persists_it(client, fake_apple):
    songs = await client.search_songs("Charlotte de Witte Doppler", limit=5)
    assert songs[0].id in {"1001", "1004"}
    assert {s.name for s in songs} >= {"Doppler", "Doppler (Radio Edit)"}
    assert songs[0].source == "apple-music"
    assert load_credentials().developer_token == fake_apple.dev_token


async def test_search_limit_is_capped_to_api_maximum(client, fake_apple):
    await client.search_songs("Charlotte", limit=500)  # fake API returns 400 above 25
    assert fake_apple.calls[-1][1].endswith("/search")


async def test_rotated_developer_token_is_refreshed_once(client, fake_apple, jwt_factory):
    client.credentials.developer_token = jwt_factory(time.time() + 86400)  # valid JWT, but stale for Apple
    songs = await client.search_songs("Amelie Lens Hypnotized")
    assert songs and songs[0].name == "Hypnotized"
    assert client.credentials.developer_token == fake_apple.dev_token


async def test_retries_on_rate_limit(client, fake_apple):
    fake_apple.fail_next = [429, 503]
    songs = await client.search_songs("Stephan Bodzin Singularity")
    assert songs[0].id == "4001"
    assert len([c for c in fake_apple.calls if c[1].endswith("/search")]) == 3


async def test_falls_back_to_itunes_search_when_amp_unavailable(client, fake_apple):
    fake_apple.amp_available = False
    songs = await client.search_songs("Boris Brejcha Gravity")
    assert songs[0].id == "5001"
    assert songs[0].source == "itunes-search"


async def test_artist_lookup_and_top_songs(client):
    artists = await client.search_artists("Amelie Lens")
    assert artists[0].name == "Amelie Lens"
    top = await client.artist_top_songs(artists[0].id, limit=2)
    assert [s.name for s in top] == ["Hypnotized", "Feel It"]


async def test_get_songs_by_id(client):
    songs = await client.get_songs(["4002", "1001", "999999"])
    assert [s.id for s in songs] == ["4002", "1001"]


async def test_user_storefront_requires_user_token(fake_apple):
    async with AppleMusicClient(Credentials(storefront="br"), persist=False) as c:
        with pytest.raises(AuthError, match="apple-music-mcp login"):
            await c.user_storefront()
    async with AppleMusicClient(Credentials(media_user_token="wrong-token-xxxxxxxx"), persist=False) as c:
        with pytest.raises(AuthError) as err:
            await c.user_storefront()
        assert err.value.status == 403


async def test_user_storefront_ok(client):
    assert await client.user_storefront() == "br"
