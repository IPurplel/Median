"""Tests for synced-lyrics lookup and .lrc sidecars.

LRCLIB is never contacted — every test stubs the HTTP layer. The point is that
a hit produces a sidecar and a miss (or an outage) changes nothing. Run with:

    py -3 -m pytest backend/tests/test_lrclib.py
"""
import io
import uuid
import zipfile

import pytest

from backend.utils import lrclib
from backend.utils.lrclib import attach_synced_lyrics, fetch_synced, write_lrc

LRC = "[00:12.50]First line\n[00:18.20]Second line\n"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """The polite gap between lookups shouldn't slow the suite down."""
    monkeypatch.setattr(lrclib.time, "sleep", lambda _s: None)


def _stub(monkeypatch, get=None, search=None, seen=None):
    def fake(path, params):
        if seen is not None:
            seen.append((path, params))
        return get if path == 'get' else search
    monkeypatch.setattr(lrclib, "_request", fake)


# ── fetch_synced ──────────────────────────────────────────────────────────────

def test_exact_match_is_used(monkeypatch):
    seen = []
    _stub(monkeypatch, get={'syncedLyrics': LRC}, seen=seen)

    assert fetch_synced("The Artist", "Song", "Album", 200) == LRC.strip()
    # A hit on /get means no need to search at all
    assert [p for p, _ in seen] == ['get']
    assert seen[0][1]['duration'] == 200


def test_falls_back_to_search_when_exact_misses(monkeypatch):
    seen = []
    _stub(monkeypatch, get=None,
          search=[{'syncedLyrics': LRC, 'duration': 201}], seen=seen)

    assert fetch_synced("The Artist", "Song", "Album", 200) == LRC.strip()
    assert [p for p, _ in seen] == ['get', 'search']


def test_search_hit_of_the_wrong_length_is_rejected(monkeypatch):
    # A remix or live cut would have the wrong timings for this recording
    _stub(monkeypatch, get=None, search=[{'syncedLyrics': LRC, 'duration': 320}])
    assert fetch_synced("The Artist", "Song", "Album", 200) is None


def test_search_hit_is_accepted_when_no_duration_is_known(monkeypatch):
    _stub(monkeypatch, get=None, search=[{'syncedLyrics': LRC, 'duration': 999}])
    assert fetch_synced("The Artist", "Song") == LRC.strip()


def test_plain_only_and_instrumental_results_are_skipped(monkeypatch):
    _stub(monkeypatch, get={'plainLyrics': 'words', 'syncedLyrics': ''})
    assert fetch_synced("A", "B", duration=100) is None

    _stub(monkeypatch, get={'syncedLyrics': LRC, 'instrumental': True})
    assert fetch_synced("A", "B", duration=100) is None


def test_outage_is_indistinguishable_from_a_miss(monkeypatch):
    _stub(monkeypatch, get=None, search=None)
    assert fetch_synced("A", "B", duration=100) is None


def test_missing_artist_or_title_never_calls_out(monkeypatch):
    seen = []
    _stub(monkeypatch, get={'syncedLyrics': LRC}, seen=seen)
    assert fetch_synced("", "Song") is None
    assert fetch_synced("Artist", "  ") is None
    assert seen == []


# ── sidecars ──────────────────────────────────────────────────────────────────

def test_write_lrc_sits_beside_the_track(tmp_path):
    track = tmp_path / "001 - Artist - Song.mp3"
    track.write_bytes(b"audio")

    assert write_lrc(track, LRC) is True
    sidecar = tmp_path / "001 - Artist - Song.lrc"
    assert sidecar.read_text(encoding='utf-8') == LRC


def test_write_lrc_ignores_empty_text(tmp_path):
    track = tmp_path / "x.mp3"
    track.write_bytes(b"audio")
    assert write_lrc(track, "   ") is False
    assert not (tmp_path / "x.lrc").exists()


def test_album_folder_gets_one_sidecar_per_track(tmp_path, monkeypatch):
    album = tmp_path / "The Artist - Album"
    album.mkdir()
    (album / "001 - First.mp3").write_bytes(b"a")
    (album / "002 - Second.mp3").write_bytes(b"b")
    (album / "cover.jpg").write_bytes(b"img")      # not audio, must be ignored

    monkeypatch.setattr(lrclib, "read_title", lambda _p: "", raising=False)
    monkeypatch.setattr("backend.utils.tag_writer.read_title", lambda _p: "")
    monkeypatch.setattr(lrclib, "fetch_synced", lambda *a, **k: LRC)

    meta = {'artist': 'The Artist', 'album': 'Album', 'tracks': [
        {'title': 'First', 'duration': 100}, {'title': 'Second', 'duration': 120}]}

    assert attach_synced_lyrics(str(album), meta, merged=False) == 2
    assert (album / "001 - First.lrc").exists()
    assert (album / "002 - Second.lrc").exists()
    assert not (album / "cover.lrc").exists()


def test_a_track_without_a_match_simply_has_no_sidecar(tmp_path, monkeypatch):
    album = tmp_path / "The Artist - Album"
    album.mkdir()
    (album / "001 - First.mp3").write_bytes(b"a")
    (album / "002 - Second.mp3").write_bytes(b"b")

    monkeypatch.setattr("backend.utils.tag_writer.read_title", lambda _p: "")
    monkeypatch.setattr(
        lrclib, "fetch_synced",
        lambda artist, title, *a, **k: LRC if title == 'First' else None,
    )

    meta = {'artist': 'The Artist', 'album': 'Album', 'tracks': [
        {'title': 'First'}, {'title': 'Second'}]}

    assert attach_synced_lyrics(str(album), meta, merged=False) == 1
    assert (album / "001 - First.lrc").exists()
    assert not (album / "002 - Second.lrc").exists()


def test_single_file_gets_its_own_sidecar(tmp_path, monkeypatch):
    track = tmp_path / "The Artist - Song.mp3"
    track.write_bytes(b"audio")
    monkeypatch.setattr(lrclib, "fetch_synced", lambda *a, **k: LRC)

    meta = {'artist': 'The Artist', 'title': 'Song', 'duration': 200}
    assert attach_synced_lyrics(str(track), meta, merged=False) == 1
    assert (tmp_path / "The Artist - Song.lrc").exists()


def test_merged_albums_are_skipped(tmp_path, monkeypatch):
    merged = tmp_path / "The Artist - Album.mp3"
    merged.write_bytes(b"audio")

    def boom(*a, **k):
        raise AssertionError("merged files must not be looked up")

    monkeypatch.setattr(lrclib, "fetch_synced", boom)
    # One file holds every track, so the timings would need re-basing per song
    assert attach_synced_lyrics(str(merged), {'artist': 'A'}, merged=True) == 0
    assert not (tmp_path / "The Artist - Album.lrc").exists()


# ── delivery ──────────────────────────────────────────────────────────────────

def test_a_single_track_with_lyrics_is_zipped_so_the_lrc_travels(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app as app_mod, db_models
    from backend.app import app

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    track = downloads / "The Artist - Song.mp3"
    track.write_bytes(b"audio")

    monkeypatch.setattr(db_models.settings, "DATABASE_PATH", str(tmp_path / "m.db"))
    monkeypatch.setattr(app_mod.settings, "UPLOAD_FOLDER", str(downloads))
    db_models.init_db()

    did = str(uuid.uuid4())
    db = db_models.get_db()
    try:
        db.execute("""INSERT INTO downloads (id, url, platform, title, artist,
                      album, status, file_path, is_playlist, include_description)
                      VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (did, 'u', 'bandcamp', 'Song', 'The Artist', 'Album',
                    'completed', str(track), 0, 0))
        db.commit()
    finally:
        db.close()

    client = TestClient(app)

    # No sidecar yet — plain audio, no archive
    r = client.get(f"/api/download/{did}/file")
    assert r.content == b"audio"

    # With one, both files have to travel together
    track.with_suffix('.lrc').write_text(LRC, encoding='utf-8')
    r = client.get(f"/api/download/{did}/file")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert sorted(names) == ['Song.lrc', 'Song.mp3']   # stems match, so it's found
