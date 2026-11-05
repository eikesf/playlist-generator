from __future__ import annotations

import pytest

from apple_music_mcp.models import Song
from apple_music_mcp.resolver import TrackRequest, resolve_tracks, score, split_title


@pytest.mark.parametrize(
    ("raw", "artist", "title"),
    [
        ("Charlotte de Witte - Doppler", "Charlotte de Witte", "Doppler"),
        ("Adam Beyer, Bart Skils - Your Mind", "Adam Beyer, Bart Skils", "Your Mind"),
        ("Energy 52 – Café del Mar (Michael Woods Remix)", "Energy 52", "Café del Mar (Michael Woods Remix)"),
        ("Opus", "", "Opus"),
    ],
)
def test_parse_requests(raw, artist, title):
    req = TrackRequest.parse(raw)
    assert (req.artist, req.title) == (artist, title)


def test_parse_dict_request():
    req = TrackRequest.parse({"artist": "Eric Prydz", "title": "Opus"})
    assert req.raw == "Eric Prydz - Opus"


def test_split_title_extracts_versions():
    assert split_title("Gravity (Ann Clue Remix)") == ("gravity", {"remix"}, "ann clue remix")
    assert split_title("Song - Radio Edit")[1] == {"radio", "edit"}
    assert split_title("Track (feat. Someone)")[0] == "track"
    assert split_title("Café del Mar")[0] == "cafe del mar"


def _song(name, artist, sid="1"):
    return Song(id=sid, name=name, artist=artist)


def test_score_penalizes_unrequested_versions_and_covers():
    req = TrackRequest.parse("Stephan Bodzin - Singularity")
    good, _ = score(req, _song("Singularity", "Stephan Bodzin"))
    karaoke, note = score(req, _song("Singularity (Karaoke Version)", "Karaoke Hits Band"))
    live, live_note = score(req, _song("Singularity (Live)", "Stephan Bodzin"))
    assert good >= 95
    assert karaoke < 65 and note
    assert live < 80 and "version differs" in live_note


def test_score_requires_the_same_remixer():
    req = TrackRequest.parse("Boris Brejcha - Gravity (Ann Clue Remix)")
    assert score(req, _song("Gravity (Ann Clue Remix)", "Boris Brejcha"))[0] >= 90
    assert score(req, _song("Gravity (Someone Else Remix)", "Boris Brejcha"))[0] < 80
    assert score(req, _song("Gravity", "Boris Brejcha"))[0] < 80


def test_score_handles_collab_artists_and_accents():
    req = TrackRequest.parse("Adam Beyer, Bart Skils - Your Mind")
    assert score(req, _song("Your Mind", "Adam Beyer & Bart Skils"))[0] >= 90
    req2 = TrackRequest.parse("Energy 52 - Cafe Del Mar (Michael Woods Remix)")
    assert score(req2, _song("Café del Mar (Michael Woods Remix)", "Energy 52"))[0] >= 90


def test_score_rejects_wrong_artist():
    req = TrackRequest.parse("Amelie Lens - Gravity")
    assert score(req, _song("Gravity", "Boris Brejcha"))[0] < 80


async def test_resolve_tracks_end_to_end(client):
    report = await resolve_tracks(
        client,
        [
            "Charlotte de Witte - Doppler",
            "Charlotte de Witte - Doppler (Radio Edit)",  # duplicate of the original
            "Stephan Bodzin - Singularity",
            "Adam Beyer, Bart Skils - Your Mind",
            "Boris Brejcha - Gravity (Ann Clue Remix)",
            "Age of Love - Age of Love (Charlotte de Witte & Enrico Sangiuliano Remix)",
            "Amelie Lens - Song That Does Not Exist",
        ],
    )
    data = report.to_dict()
    assert report.song_ids == ["1001", "4001", "3001", "5002", "6001"]
    assert data["summary"]["matched"] == 5
    assert data["summary"]["not_found"] + data["summary"]["low_confidence"] == 1
    assert data["summary"]["duplicates_removed"] == 1
    assert data["duplicates"][0]["duplicate_of"] == "Charlotte de Witte - Doppler"
    assert report.total_duration_ms == 372000 + 467000 + 418000 + 444000 + 385000
    missing = [t for t in data["tracks"] if t["status"] != "matched"]
    assert missing[0]["request"] == "Amelie Lens - Song That Does Not Exist"
    assert "song" not in missing[0] or missing[0]["status"] == "low_confidence"
