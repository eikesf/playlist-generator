from __future__ import annotations

import pytest

from apple_music_mcp.apple.errors import AppleMusicError
from apple_music_mcp.editing import STATE_KEY, TrackRef, manual_plan


async def _playlist(app, ids, name="Set"):
    pl = await app.library.create_playlist(name, "desc")
    await app.library.add_tracks(pl.id, ids)
    return pl


def _deletes(fake):
    return [c for c in fake.calls if c[0] == "DELETE" and "/tracks" in c[1]]


async def test_reorder_in_place_keeps_the_same_playlist(app, fake_apple):
    pl = await _playlist(app, ["4001", "1001", "2001", "5001"])
    result = await app.editor.sync(pl.id, ["4001", "2001", "1001", "5001"])
    assert result.strategy == "in_place" and result.verified and result.changed
    assert result.playlist.id == pl.id
    assert fake_apple.playlists[pl.id]["tracks"] == ["4001", "2001", "1001", "5001"]
    assert result.removed == 3  # common prefix "4001" is kept untouched
    assert app.state.get(STATE_KEY) == "in_place"


async def test_append_only_does_not_remove_anything(app, fake_apple):
    pl = await _playlist(app, ["4001", "1001"])
    result = await app.editor.sync(pl.id, ["4001", "1001", "2001", "7001"])
    assert result.removed == 0 and result.added == 2
    assert _deletes(fake_apple) == []
    assert fake_apple.playlists[pl.id]["tracks"] == ["4001", "1001", "2001", "7001"]


async def test_sync_adds_removes_and_reorders(app, fake_apple):
    pl = await _playlist(app, ["4001", "1001", "2001"])
    result = await app.editor.sync(pl.id, ["2001", "7001", "4001"])
    assert result.verified
    assert fake_apple.playlists[pl.id]["tracks"] == ["2001", "7001", "4001"]


async def test_no_op_when_already_in_order(app, fake_apple):
    pl = await _playlist(app, ["4001", "1001"])
    calls = len(fake_apple.calls)
    result = await app.editor.sync(pl.id, ["4001", "1001"])
    assert not result.changed and result.strategy == "none"
    assert all(c[0] == "GET" for c in fake_apple.calls[calls:])


async def test_falls_back_to_rebuild_when_in_place_is_refused(app, fake_apple):
    fake_apple.remove_variants = set()
    pl = await _playlist(app, ["4001", "1001", "2001"], name="Techno")
    result = await app.editor.sync(pl.id, ["2001", "1001", "4001"], allow_rebuild=True)
    assert result.strategy == "rebuild" and result.verified
    assert result.playlist.id != pl.id and result.playlist.name == "Techno"
    assert pl.id not in fake_apple.playlists  # old one deleted
    assert fake_apple.playlists[result.playlist.id]["tracks"] == ["2001", "1001", "4001"]
    assert fake_apple.playlists[result.playlist.id]["description"] == "desc"
    assert "new link" in result.warnings[0]


async def test_rebuild_renames_old_copy_when_delete_is_refused(app, fake_apple):
    fake_apple.remove_variants = set()
    fake_apple.allow_delete_playlist = False
    pl = await _playlist(app, ["4001", "1001"], name="Run")
    result = await app.editor.sync(pl.id, ["1001", "4001"], allow_rebuild=True)
    assert fake_apple.playlists[pl.id]["name"] == "Run (old)"
    assert any("(old)" in w for w in result.warnings)


async def test_refused_in_place_without_confirmation_asks_instead_of_rebuilding(app, fake_apple):
    fake_apple.remove_variants = set()
    pl = await _playlist(app, ["4001", "1001", "2001"], name="Techno")
    result = await app.editor.sync(pl.id, ["2001", "4001", "1001"])  # default: no rebuild
    assert result.status == "needs_decision"
    assert len(fake_apple.playlists) == 1  # nothing recreated
    assert fake_apple.playlists[pl.id]["tracks"] == ["4001", "1001", "2001"]  # nothing removed or moved
    d = result.decision
    assert d["changes"] == 1 and d["size"] == "small" and d["recommendation"].startswith("manual")
    assert d["manual_steps"] == ["Mover 'Amelie Lens - Hypnotized' para o início"]


async def test_needs_decision_appends_new_tracks_and_lists_moves(app, fake_apple):
    fake_apple.remove_variants = set()
    pl = await _playlist(app, ["4001", "1001"])
    result = await app.editor.sync(pl.id, ["4001", "7001", "1001"])
    assert result.status == "needs_decision" and result.added == 1
    assert fake_apple.playlists[pl.id]["tracks"] == ["4001", "1001", "7001"]  # appended, nothing lost
    assert result.decision["manual_steps"] == [
        "Mover 'Eric Prydz - Opus' para logo depois de 'Stephan Bodzin - Singularity'"
    ]


async def test_large_change_recommends_rebuild(app, fake_apple):
    fake_apple.remove_variants = set()
    ids = ["4001", "4002", "2001", "2002", "2003", "1001", "1002", "1003", "5001"]
    pl = await _playlist(app, ids)
    result = await app.editor.sync(pl.id, list(reversed(ids)))
    assert result.decision["size"] == "large"
    assert result.decision["recommendation"].startswith("rebuild")
    assert result.decision["moves"] == 8  # reversing n tracks keeps 1 in place


async def test_midway_failure_that_cannot_be_restored_says_so(app, fake_apple):
    pl = await _playlist(app, ["4001", "1001", "2001"])
    fake_apple.fail_add_after = fake_apple._adds  # every later add fails, so restore fails too
    with pytest.raises(AppleMusicError, match="could NOT be fully restored"):
        await app.editor.sync(pl.id, ["2001", "1001", "4001"], allow_rebuild=False)


async def test_midway_failure_is_restored_when_apple_recovers(app, fake_apple, monkeypatch):
    pl = await _playlist(app, ["4001", "1001", "2001"])
    original_add = app.library.add_tracks
    calls = {"n": 0}

    async def flaky_add(playlist_id, ids, kind="songs"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AppleMusicError("boom", 500)
        return await original_add(playlist_id, ids, kind)

    monkeypatch.setattr(app.library, "add_tracks", flaky_add)
    with pytest.raises(AppleMusicError, match="restored"):
        await app.editor.sync(pl.id, ["2001", "1001", "4001"], allow_rebuild=False)
    assert fake_apple.playlists[pl.id]["tracks"] == ["4001", "1001", "2001"]


async def test_duplicate_in_prefix_forces_full_rewrite(app, fake_apple):
    pl = await _playlist(app, ["4001", "1001", "4001"])  # same song twice
    result = await app.editor.sync(pl.id, ["4001", "2001"])
    assert result.verified
    assert fake_apple.playlists[pl.id]["tracks"] == ["4001", "2001"]


def test_track_ref_parsing():
    assert TrackRef.parse("1001") == TrackRef("1001", "songs")
    assert TrackRef.parse("i.ABC") == TrackRef("i.ABC", "library-songs")


def test_manual_plan_removals_and_moves():
    from apple_music_mcp.models import PlaylistEntry

    cur = [PlaylistEntry(f"i.{x}", x, x.upper(), "Art") for x in ["a", "b", "c", "d"]]
    plan = manual_plan(cur, [TrackRef.parse(x) for x in ["c", "a", "b"]])
    assert plan["removals"] == 1 and plan["moves"] == 1
    assert plan["manual_steps"] == ["Remover 'Art - D'", "Mover 'Art - C' para o início"]
