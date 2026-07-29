"""Tests for per-track selection on album downloads.

Covers the metadata narrowing in backend.app._apply_track_selection, the
request validator, and what the /api/download endpoint hands to the queue.
No network or yt-dlp involved. Run with:

    py -3 -m pytest backend/tests/test_track_selection.py
"""
import pytest
from fastapi import HTTPException

from backend.app import _apply_track_selection


def _meta(n=5, is_playlist=True):
    return {
        'is_playlist': is_playlist,
        'title': 'Some Album',
        'artist': 'Some Artist',
        'track_count': n,
        'total_duration': n * 100,
        'tracks': [
            {'index': i, 'title': f'Track {i}', 'duration': 100,
             'url': f'https://a.bandcamp.com/track/{i}'}
            for i in range(1, n + 1)
        ],
    }


# ── _apply_track_selection ────────────────────────────────────────────────────

def test_no_selection_leaves_metadata_untouched():
    meta = _meta()
    out, indices = _apply_track_selection(meta, None)
    assert indices is None
    assert out is meta


def test_selecting_every_track_is_treated_as_no_selection():
    # Downloading all 5 of 5 should take the ordinary whole-album path
    meta = _meta(5)
    out, indices = _apply_track_selection(meta, [1, 2, 3, 4, 5])
    assert indices is None
    assert out is meta


def test_subset_filters_tracks_and_recounts():
    meta = _meta(5)
    out, indices = _apply_track_selection(meta, [2, 4])

    assert indices == [2, 4]
    assert [t['title'] for t in out['tracks']] == ['Track 2', 'Track 4']
    assert out['track_count'] == 2
    assert out['total_duration'] == 200


def test_original_metadata_is_not_mutated():
    # The metadata dict comes from a shared cache — narrowing one download's
    # copy must not shrink the album for everyone else.
    meta = _meta(5)
    _apply_track_selection(meta, [1])
    assert len(meta['tracks']) == 5
    assert meta['track_count'] == 5


def test_out_of_range_numbers_are_ignored():
    meta = _meta(3)
    out, indices = _apply_track_selection(meta, [2, 99])
    assert indices == [2]
    assert [t['title'] for t in out['tracks']] == ['Track 2']


def test_all_numbers_out_of_range_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _apply_track_selection(_meta(3), [7, 8])
    assert exc.value.status_code == 400


def test_single_track_downloads_ignore_selection():
    meta = _meta(1, is_playlist=False)
    meta['tracks'] = []
    out, indices = _apply_track_selection(meta, [1])
    assert indices is None
    assert out is meta


# ── request validation + endpoint wiring ──────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.app import app
    return TestClient(app)


@pytest.fixture
def stub(monkeypatch):
    from backend import app as app_mod

    captured = {}

    async def fake_extract(url):
        return _meta(5)

    async def fake_enqueue(params):
        captured.update(params)
        return 'dl-id'

    monkeypatch.setattr(app_mod, "extract_metadata", fake_extract)
    monkeypatch.setattr(app_mod, "enqueue_download", fake_enqueue)
    return captured


def _body(**extra):
    body = {'url': 'https://a.bandcamp.com/album/x',
            'download_type': 'audio', 'format': 'mp3'}
    body.update(extra)
    return body


def test_endpoint_forwards_a_partial_selection(client, stub):
    r = client.post("/api/download", json=_body(selected_tracks=[3, 1]))
    assert r.status_code == 200

    # Sorted and deduped by the validator before reaching the queue
    assert stub['selected_indices'] == [1, 3]
    assert [t['title'] for t in stub['metadata']['tracks']] == ['Track 1', 'Track 3']
    assert stub['metadata']['track_count'] == 2


def test_endpoint_dedupes_and_sorts(client, stub):
    r = client.post("/api/download", json=_body(selected_tracks=[4, 2, 4]))
    assert r.status_code == 200
    assert stub['selected_indices'] == [2, 4]


def test_endpoint_without_selection_sends_none(client, stub):
    r = client.post("/api/download", json=_body())
    assert r.status_code == 200
    assert stub['selected_indices'] is None
    assert len(stub['metadata']['tracks']) == 5


def test_non_positive_track_numbers_are_rejected(client, stub):
    r = client.post("/api/download", json=_body(selected_tracks=[0, -2]))
    assert r.status_code == 422
    assert stub == {}


def test_selection_larger_than_the_track_cap_is_rejected(client, stub, monkeypatch):
    from backend import app as app_mod
    monkeypatch.setattr(app_mod.settings, "MAX_PLAYLIST_TRACKS", 3)
    r = client.post("/api/download", json=_body(selected_tracks=[1, 2, 3, 4]))
    assert r.status_code == 422
    assert stub == {}
