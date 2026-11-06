"""Guide §7.4: known = loved or >= 10 plays; 1-9 plays = partly known; known artist = has a known track."""

from __future__ import annotations

from apple_music_mcp.config import Credentials
from apple_music_mcp.container import AppContainer
from apple_music_mcp.curation.familiarity import MixTargets
from apple_music_mcp.curation.known import FamiliarityIndex, ListeningFacts
from apple_music_mcp.db.repositories import Counters, match_key
from apple_music_mcp.jobs import run_job
from apple_music_mcp.local_music import SEP, MusicAppReader
from apple_music_mcp.resolver import resolve_tracks

from .conftest import build_app
from .fake_apple import FakeAppleMusic


def c(played: int, favorited: bool = False) -> Counters:
    return Counters(played, 0, favorited, False)


def index(**counters: Counters) -> FamiliarityIndex:
    keyed = {match_key(*k.split("__")): v for k, v in counters.items()}
    return FamiliarityIndex(ListeningFacts(counters=keyed, loved_ids={"loved-id"}, library_ids={"lib-id"}))


def test_definitions_from_the_guide() -> None:
    idx = index(
        **{
            "Amelie Lens__Feel It": c(12),
            "Amelie Lens__Hypnotized": c(3),
            "Boris Brejcha__Gravity": c(0, True),
        }
    )
    assert idx.definitions == "guide"
    assert idx.bucket("x", "Amelie Lens", "Feel It") == "known_track"  # >= 10 plays
    assert idx.bucket("x", "Amelie Lens", "Hypnotized") == "partly_known"  # 1-9 plays, not loved
    assert idx.bucket("x", "Boris Brejcha", "Gravity") == "known_track"  # favorite in the Music app
    assert idx.bucket("loved-id", "Someone", "Song") == "known_track"  # loved rating
    assert idx.bucket("lib-id", "Someone", "Other") == "partly_known"  # in the library, never loved
    assert idx.bucket("x", "Amelie Lens", "Never Heard") == "known_artist_new_track"
    assert idx.bucket("x", "Boris Brejcha", "Never Heard") == "known_artist_new_track"
    assert idx.bucket("x", "New Person", "Track") == "new_artist"


def test_an_artist_with_only_partly_known_songs_is_not_known() -> None:
    idx = index(**{"Charlotte de Witte__Doppler": c(9)})
    assert idx.bucket("x", "Charlotte de Witte", "Sgadi Li Mi") == "new_artist"


def test_fallback_without_play_data() -> None:
    idx = FamiliarityIndex(ListeningFacts(library_ids={"lib-id"}, known_artist_names={"Amelie Lens"}))
    assert idx.definitions == "fallback"
    assert idx.bucket("lib-id", "X", "Y") == "known_track"
    assert idx.bucket("z", "Amelie Lens", "Y") == "known_artist_new_track"


def test_two_level_targets() -> None:
    t = MixTargets.from_levels(50, 30)
    assert (t.known_artists_pct, t.known_tracks_pct, t.known_within_known_artists_pct) == (50, 15, 30)
    assert MixTargets.default() == t


async def test_resolve_uses_snapshot_play_counts(
    fake_apple: FakeAppleMusic, logged_in_creds: Credentials
) -> None:
    song = fake_apple.songs_by_id["1001"]
    line = SEP.join(
        ["P1", song.name, song.artist, "missing value", "Techno", "300", "15", "0", "false", "false"]
    )

    async def runner(args: list[str]) -> tuple[int, str, str]:
        return 0, line + "\n", ""

    async with build_app(logged_in_creds, music_app=MusicAppReader(runner=runner, system="Darwin")) as app:
        await run_job("snapshot", app)
        report = await resolve_tracks(app.catalog, [f"{song.artist} - {song.name}"])
        data = await app.discovery.annotate(report, MixTargets.from_levels(100, 100))
    row = data["tracks"][0]
    assert row["familiarity"] == "known_track" and row["plays"] == 15
    assert data["summary"]["familiarity_definitions"] == "guide"
    assert data["summary"]["known_tracks_within_known_artists_pct"] == 100


async def test_default_container_wires_min_plays(app: AppContainer) -> None:
    assert app.discovery.min_plays == app.settings.known_min_plays == 10
