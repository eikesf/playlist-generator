from __future__ import annotations

import random
import time

import pytest

from apple_music_mcp.curation.planner import distribute, plan_playlist
from apple_music_mcp.curation.profiles import PROFILES, get_profile
from apple_music_mcp.curation.sequencer import Sequencer, TrackFeatures, bpm_distance, order_tracks


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("dj_set", "dj_set"),
        ("Set de techno estilo DJ", "dj_set"),
        ("melodic techno", "dj_set"),
        ("playlist para treino na academia", "workout"),
        ("corrida 10k", "running"),
        ("música para estudar e ter foco", "study"),
        ("festa de aniversário", "party"),
        ("algo para dormir", "sleep"),
        ("domingo tranquilo", "chill"),
        ("quero conhecer jazz", "exploration"),
        ("", "exploration"),
        ("something unknown", "exploration"),
    ],
)
def test_profile_resolution_pt_and_en(text, key):
    assert get_profile(text).key == key


def test_every_profile_is_consistent():
    for p in PROFILES.values():
        assert abs(sum(a.share for a in p.acts) - 1) < 1e-9
        assert p.bpm_range[0] < p.bpm_range[1]
        assert all(1 <= a.energy_start <= 10 and 1 <= a.energy_end <= 10 for a in p.acts)
        assert p.energy_at(0) == p.acts[0].energy_start
        assert p.energy_at(1) == p.acts[-1].energy_end


def test_study_profile_has_no_peaks_and_workout_stays_high():
    study = PROFILES["study"]
    assert max(study.energy_at(t / 100) for t in range(101)) <= 4
    workout = PROFILES["workout"]
    assert min(workout.energy_at(t / 100) for t in range(15, 85)) >= 8


def test_distribute_is_exact_and_proportional():
    assert sum(distribute(19, [0.15, 0.2, 0.25, 0.25, 0.15])) == 19
    assert distribute(10, [0.1, 0.8, 0.1]) == [1, 8, 1]
    assert distribute(2, [0.5, 0.3, 0.2]) == [1, 1, 1]  # every act gets at least one track


def test_plan_two_hour_techno_set():
    plan = plan_playlist(get_profile("set de techno"), duration_minutes=120).to_dict()
    assert plan["purpose"] == "dj_set"
    assert plan["total_tracks"] == 18  # 120 / 6.5
    assert [a["name"] for a in plan["acts"]][3] == "Peak"
    assert sum(a["tracks"] for a in plan["acts"]) == 18
    peak = plan["acts"][3]
    assert peak["target_bpm"][1] == 140 and peak["energy"] == [8.5, 10]


def test_plan_defaults_and_track_count():
    assert plan_playlist(PROFILES["exploration"]).total_tracks == 75
    plan = plan_playlist(PROFILES["workout"], track_count=20)
    assert plan.total_tracks == 20 and round(plan.total_minutes) == 76


def test_bpm_distance_half_double_time():
    assert bpm_distance(87, 174) == 0
    assert bpm_distance(124, 128) == 4


def _techno_pool() -> list[TrackFeatures]:
    spec = [
        ("a", "Stephan Bodzin", 121, 3),
        ("b", "Tale Of Us", 122, 3.5),
        ("c", "Mind Against", 123, 4),
        ("d", "Amelie Lens", 126, 5),
        ("e", "Adam Beyer", 127, 5.5),
        ("f", "Charlotte de Witte", 128, 6),
        ("g", "Maceo Plex", 128, 6.5),
        ("h", "Anyma", 129, 7),
        ("i", "Boris Brejcha", 130, 7.5),
        ("j", "Charlotte de Witte", 133, 8.5),
        ("k", "Amelie Lens", 134, 9),
        ("l", "I Hate Models", 138, 10),
        ("m", "Adam Beyer", 136, 9.5),
        ("n", "Stephan Bodzin", 126, 6),
        ("o", "Tale Of Us", 124, 4.5),
    ]
    return [
        TrackFeatures(id=i, artist=a, title=i, bpm=b, energy=e, duration_ms=390_000) for i, a, b, e in spec
    ]


def test_dj_set_follows_the_energy_curve():
    pool = _techno_pool()
    random.Random(7).shuffle(pool)
    result = order_tracks(PROFILES["dj_set"], pool)
    rows = result["tracks"]
    energies = [r["energy"] for r in rows]
    assert len(rows) == 15 and len(set(result["song_ids_in_order"])) == 15
    assert max(energies[:2]) <= 4  # atmospheric opening
    peak_pos = energies.index(10)
    assert 0.5 <= peak_pos / len(rows) <= 0.85  # peak in the second half, before the cooldown
    assert energies[-1] < 8  # ends cooling down
    assert rows[-1]["act"] == "Cooldown" and rows[0]["act"] == "Atmospheric opening"
    names = [r["track"].split(" - ")[0] for r in rows]
    assert all(names[i] != names[i + 1] for i in range(len(names) - 1))  # no back-to-back artist


def test_order_is_deterministic_regardless_of_input_order():
    a, b = _techno_pool(), _techno_pool()
    random.Random(1).shuffle(a)
    random.Random(2).shuffle(b)
    profile = PROFILES["dj_set"]
    assert order_tracks(profile, a)["song_ids_in_order"] == order_tracks(profile, b)["song_ids_in_order"]


def test_workout_puts_low_energy_tracks_at_the_edges():
    energies = [5, 6, 8, 8, 9, 9, 8, 9, 8, 9, 8.5, 9, 8, 5, 4]  # 2 warm-up, 11 high, 2 cooldown
    tracks = [
        TrackFeatures(id=str(i), artist=f"A{i}", bpm=125 + e, energy=e, duration_ms=210_000)
        for i, e in enumerate(energies)
    ]
    rows = order_tracks(PROFILES["workout"], tracks)["tracks"]
    edges = [rows[i]["energy"] for i in (0, 1, -2, -1)]
    assert sorted(edges) == [4, 5, 5, 6]  # all low-energy tracks at warm-up / cooldown
    assert min(r["energy"] for r in rows[2:-2]) >= 8  # the middle never dips


def test_forced_act_is_respected():
    pool = _techno_pool()
    pool[0].act = "Peak"  # Stephan Bodzin energy 3 forced into the peak
    rows = order_tracks(PROFILES["dj_set"], pool)["tracks"]
    assert next(r for r in rows if r["id"] == "a")["act"] == "Peak"


def test_missing_estimates_are_tolerated_and_reported():
    pool = _techno_pool()
    pool[3].bpm = pool[3].energy = None
    result = order_tracks(PROFILES["dj_set"], pool)
    assert len(result["song_ids_in_order"]) == 15
    assert any("no BPM/energy" in w for w in result["warnings"])


def test_duplicates_and_tiny_inputs():
    t = TrackFeatures(id="x", artist="A", bpm=120, energy=5)
    assert Sequencer(PROFILES["chill"]).order([t, t]) == [t]
    assert order_tracks(PROFILES["chill"], [])["song_ids_in_order"] == []


def test_sequencing_100_tracks_is_fast():
    rnd = random.Random(3)
    tracks = [
        TrackFeatures(
            id=str(i),
            artist=f"Artist {i % 30}",
            bpm=rnd.uniform(118, 140),
            energy=rnd.uniform(2, 10),
            duration_ms=rnd.randint(300_000, 480_000),
        )
        for i in range(100)
    ]
    start = time.perf_counter()
    result = order_tracks(PROFILES["dj_set"], tracks)
    assert time.perf_counter() - start < 10
    assert sorted(result["song_ids_in_order"], key=int) == [str(i) for i in range(100)]
