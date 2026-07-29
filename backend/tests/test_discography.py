"""Tests for artist-discography resolution and lazy album metadata.

Covers the URL→artist-page mapping, album filtering/dedupe in
backend.discography, and the queue's deferred metadata resolution for albums
queued from a discography listing. No network or yt-dlp involved. Run with:

    py -3 -m pytest backend/tests/test_discography.py
"""
import asyncio

import pytest

from backend import discography as disco
from backend.discography import (
    _clean_url, _entries_to_albums, _is_album_entry, _title_from_url,
    artist_pages, resolve_discography,
)


def _entry(url, title=None, **extra):
    e = {'url': url}
    if title is not None:
        e['title'] = title
    e.update(extra)
    return e


# ── artist_pages ──────────────────────────────────────────────────────────────

def test_bandcamp_album_maps_to_music_page():
    assert artist_pages("https://sundara.bandcamp.com/album/deep-blue") == [
        "https://sundara.bandcamp.com/music"
    ]


def test_bandcamp_host_is_lowercased():
    pages = artist_pages("https://SunDara.Bandcamp.com/album/x")
    assert pages == ["https://sundara.bandcamp.com/music"]


def test_soundcloud_set_maps_to_user_release_pages():
    assert artist_pages("https://soundcloud.com/some-artist/sets/first-ep") == [
        "https://soundcloud.com/some-artist/albums",
        "https://soundcloud.com/some-artist/sets",
    ]


def test_youtube_uses_channel_url_from_metadata():
    pages = artist_pages(
        "https://www.youtube.com/playlist?list=OLAK5uy_abc",
        {'artist_url': 'https://www.youtube.com/channel/UC123/'},
    )
    assert pages == [
        "https://www.youtube.com/channel/UC123/releases",
        "https://www.youtube.com/channel/UC123/playlists",
    ]


def test_youtube_without_channel_url_has_no_pages():
    # A playlist URL alone doesn't identify the artist — nothing to look up.
    assert artist_pages("https://www.youtube.com/playlist?list=OLAK5uy_abc", {}) == []


def test_unsupported_url_has_no_pages():
    assert artist_pages("https://example.com/album/x") == []


# ── URL/title helpers ─────────────────────────────────────────────────────────

def test_clean_url_drops_query_fragment_and_trailing_slash():
    assert (_clean_url("https://a.bandcamp.com/album/x/?from=hp#t1")
            == "https://a.bandcamp.com/album/x")


def test_clean_url_keeps_the_youtube_playlist_id():
    # A YouTube album is identified only by ?list= — dropping it would collapse
    # every album on a channel into one unusable /playlist URL.
    assert (_clean_url("https://www.youtube.com/playlist?list=OLAK5uy_A&si=xyz")
            == "https://www.youtube.com/playlist?list=OLAK5uy_A")


def test_distinct_youtube_albums_stay_distinct():
    entries = [
        _entry("https://www.youtube.com/playlist?list=OLAK5uy_A", "Album A"),
        _entry("https://www.youtube.com/playlist?list=OLAK5uy_B", "Album B"),
        _entry("https://www.youtube.com/playlist?list=OLAK5uy_A&si=dupe", "Album A again"),
    ]
    albums = _entries_to_albums(entries, 'youtube')
    assert [a['title'] for a in albums] == ["Album A", "Album B"]


def test_title_from_url_slug():
    assert _title_from_url("https://a.bandcamp.com/album/deep-blue-ep") == "Deep Blue Ep"


def test_title_from_url_handles_empty_slug():
    assert _title_from_url("https://a.bandcamp.com/") == "Untitled Album"


# ── entry filtering ───────────────────────────────────────────────────────────

def test_bandcamp_singles_are_not_albums():
    assert _is_album_entry(_entry("https://a.bandcamp.com/album/x"), 'bandcamp')
    assert not _is_album_entry(_entry("https://a.bandcamp.com/track/y"), 'bandcamp')


def test_youtube_and_soundcloud_album_shapes():
    assert _is_album_entry(_entry("https://youtube.com/playlist?list=A"), 'youtube')
    assert not _is_album_entry(_entry("https://youtube.com/watch?v=A"), 'youtube')
    assert _is_album_entry(_entry("https://soundcloud.com/u/sets/a"), 'soundcloud')
    assert not _is_album_entry(_entry("https://soundcloud.com/u/track"), 'soundcloud')


def test_relative_and_empty_entries_are_dropped():
    assert not _is_album_entry(_entry("/album/x"), 'bandcamp')
    assert not _is_album_entry(None, 'bandcamp')


def test_entries_to_albums_dedupes_and_falls_back_to_slug_title():
    entries = [
        _entry("https://a.bandcamp.com/album/one", "First Album", playlist_count=8),
        _entry("https://a.bandcamp.com/album/one/?from=hp"),   # same album, query
        _entry("https://a.bandcamp.com/track/single"),          # not an album
        _entry("https://a.bandcamp.com/album/two-ep"),          # no title reported
    ]
    albums = _entries_to_albums(entries, 'bandcamp')

    assert [a['url'] for a in albums] == [
        "https://a.bandcamp.com/album/one",
        "https://a.bandcamp.com/album/two-ep",
    ]
    assert albums[0]['title'] == "First Album"
    assert albums[0]['track_count'] == 8
    assert albums[0]['title_is_guess'] is False
    # No title on the listing page — guess from the slug, flagged as a guess so
    # the real title can replace it once the album's metadata is extracted.
    assert albums[1]['title'] == "Two Ep"
    assert albums[1]['title_is_guess'] is True


def test_entries_to_albums_respects_the_cap(monkeypatch):
    monkeypatch.setattr(disco.settings, "MAX_DISCOGRAPHY_ALBUMS", 3)
    entries = [_entry(f"https://a.bandcamp.com/album/{i}") for i in range(10)]
    assert len(_entries_to_albums(entries, 'bandcamp')) == 3


# ── Bandcamp real-title enrichment ────────────────────────────────────────────

# Trimmed to the parts the scraper reads: the server-rendered grid (first
# screen of releases) and the JSON blob holding the lazy-loaded remainder.
_BC_PAGE = """
<ol id="music-grid" data-client-items="[{&quot;page_url&quot;:&quot;/album/twin-fantasy&quot;,
  &quot;title&quot;:&quot;Twin Fantasy&quot;},{&quot;page_url&quot;:&quot;/album/4&quot;,&quot;title&quot;:&quot;4&quot;}]">
  <li data-item-id="album-1">
    <a href="https://a.bandcamp.com/album/madlo-influences">
      <p class="title">MADLO: Influences</p></a>
  </li>
  <li data-item-id="album-2">
    <a href="/album/we-looked-like-giants">
      <p class="title">We Looked Like Giants
        <span class="artist-override">Some Guest Artist</span></p></a>
  </li>
</ol>
"""


def test_bandcamp_titles_read_grid_and_json_blob():
    titles = disco._bandcamp_titles(_BC_PAGE)
    assert titles["/album/madlo-influences"] == "MADLO: Influences"
    assert titles["/album/twin-fantasy"] == "Twin Fantasy"
    assert titles["/album/4"] == "4"
    # A featured-artist line inside the title block is not part of the title
    assert titles["/album/we-looked-like-giants"] == "We Looked Like Giants"


class _FakeResponse:
    """Minimal stand-in for urllib's urlopen context manager."""

    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_enrichment_replaces_guessed_titles_only(monkeypatch):
    albums = [
        {'url': 'https://a.bandcamp.com/album/twin-fantasy',
         'title': 'Twin Fantasy Slug', 'title_is_guess': True},
        {'url': 'https://a.bandcamp.com/album/known',
         'title': 'Already Correct', 'title_is_guess': False},
        {'url': 'https://a.bandcamp.com/album/not-on-page',
         'title': 'Not On Page', 'title_is_guess': True},
    ]

    import urllib.request
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _FakeResponse(_BC_PAGE)
    )
    asyncio.run(disco._enrich_bandcamp_titles("https://a.bandcamp.com/music", albums))

    assert albums[0]['title'] == 'Twin Fantasy'
    assert albums[0]['title_is_guess'] is False
    assert albums[1]['title'] == 'Already Correct'   # untouched
    assert albums[2]['title'] == 'Not On Page'       # keeps the slug guess
    assert albums[2]['title_is_guess'] is True


def test_enrichment_survives_a_failed_fetch(monkeypatch):
    albums = [{'url': 'https://a.bandcamp.com/album/x',
               'title': 'X', 'title_is_guess': True}]

    import urllib.request

    def _boom(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    asyncio.run(disco._enrich_bandcamp_titles("https://a.bandcamp.com/music", albums))
    assert albums[0]['title'] == 'X'  # still downloadable, just a slug title


# ── resolve_discography ───────────────────────────────────────────────────────

@pytest.fixture
def no_cache(monkeypatch):
    """Bypass the SQLite-backed metadata cache — these tests only care about
    page selection and parsing."""
    monkeypatch.setattr(disco.metadata_cache, "get", lambda _k: None)
    monkeypatch.setattr(disco.metadata_cache, "set", lambda *a, **k: None)


def test_resolve_falls_through_to_the_second_page(no_cache, monkeypatch):
    seen = []

    async def fake_flat(page):
        seen.append(page)
        if page.endswith('/albums'):
            return []                       # account has no "albums" tab
        return [_entry("https://soundcloud.com/u/sets/ep-one", "EP One")]

    monkeypatch.setattr(disco, "_flat_entries", fake_flat)
    result = asyncio.run(
        resolve_discography("https://soundcloud.com/u/sets/ep-one", {'artist': 'U'})
    )

    assert seen == [
        "https://soundcloud.com/u/albums",
        "https://soundcloud.com/u/sets",
    ]
    assert result['source_page'] == "https://soundcloud.com/u/sets"
    assert [a['title'] for a in result['albums']] == ["EP One"]
    assert result['artist'] == 'U'


def test_resolve_with_no_albums_is_not_an_error(no_cache, monkeypatch):
    async def empty(_page):
        return []

    monkeypatch.setattr(disco, "_flat_entries", empty)
    result = asyncio.run(resolve_discography("https://a.bandcamp.com/album/x"))

    assert result['albums'] == []
    assert 'note' in result


def test_resolve_without_an_artist_page_skips_extraction(no_cache, monkeypatch):
    async def boom(_page):
        raise AssertionError("should not extract without a candidate page")

    monkeypatch.setattr(disco, "_flat_entries", boom)
    result = asyncio.run(resolve_discography("https://example.com/album/x"))
    assert result['albums'] == []


def test_resolve_uses_the_cache(monkeypatch):
    cached = {'platform': 'bandcamp', 'artist': 'A', 'albums': [{'url': 'u', 'title': 't'}]}
    monkeypatch.setattr(disco.metadata_cache, "get", lambda _k: cached)

    async def boom(_page):
        raise AssertionError("cache hit should skip extraction")

    monkeypatch.setattr(disco, "_flat_entries", boom)
    assert asyncio.run(
        resolve_discography("https://a.bandcamp.com/album/x")
    ) == cached


# ── API endpoints ─────────────────────────────────────────────────────────────
# TestClient is used without its context manager on purpose: entering it would
# run the app lifespan (scheduler, yt-dlp self-update, aiohttp session), none of
# which these request/response tests need.

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.app import app
    return TestClient(app)


@pytest.fixture
def stub_backend(monkeypatch):
    """No network, no queue — capture what the endpoints would have enqueued."""
    from backend import app as app_mod

    enqueued = []

    async def fake_extract(url):
        return {'artist': 'Test Artist', 'is_playlist': True, 'title': 'Some Album'}

    async def fake_enqueue(params):
        enqueued.append(params)
        return f"id-{len(enqueued)}"

    monkeypatch.setattr(app_mod, "extract_metadata", fake_extract)
    monkeypatch.setattr(app_mod, "enqueue_download", fake_enqueue)
    return enqueued


def test_discography_endpoint_rejects_unsupported_urls(client, stub_backend):
    r = client.post("/api/discography", json={'url': 'https://example.com/album/x'})
    assert r.status_code == 400


def test_discography_endpoint_returns_albums(client, stub_backend, monkeypatch):
    async def fake_resolve(url, meta=None, force_refresh=False):
        return {'platform': 'bandcamp', 'artist': '', 'source_page': 'p',
                'albums': [{'url': 'https://a.bandcamp.com/album/one', 'title': 'One'}]}

    monkeypatch.setattr(disco, "resolve_discography", fake_resolve)
    r = client.post("/api/discography", json={'url': 'https://a.bandcamp.com/album/x'})

    assert r.status_code == 200
    body = r.json()
    assert body['platform'] == 'bandcamp'
    # The resolver found no artist name — fall back to the album's metadata
    assert body['artist'] == 'Test Artist'
    assert len(body['albums']) == 1


def test_batch_download_queues_one_per_album(client, stub_backend):
    r = client.post("/api/discography/download", json={
        'url': 'https://a.bandcamp.com/album/x',
        'download_type': 'audio', 'format': 'mp3', 'bitrate': '320',
        'include_description': True,
        'cover_id': 'ec1f0d4c-3a4e-4a4c-9f2f-2a3c1e6d5b7a',
        'albums': [
            {'url': 'https://a.bandcamp.com/album/one', 'title': 'One'},
            {'url': 'https://a.bandcamp.com/album/two', 'title': 'Two'},
            {'url': 'https://a.bandcamp.com/album/one'},        # duplicate
            {'url': 'https://example.com/nope', 'title': 'Nope'},  # unsupported
        ],
    })

    assert r.status_code == 200
    body = r.json()
    assert [q['title'] for q in body['queued']] == ['One', 'Two']
    assert [s['url'] for s in body['skipped']] == ['https://example.com/nope']

    first = stub_backend[0]
    assert first['url'] == 'https://a.bandcamp.com/album/one'
    assert first['include_description'] is True
    assert first['format'] == 'mp3'
    # One uploaded cover can't apply to a whole discography
    assert first['cover_id'] is None
    assert first['source'] == 'discography'
    # Queued with a placeholder; the queue fills in the real tracklist
    assert first['metadata']['needs_resolve'] is True
    assert first['metadata']['artist'] == 'Test Artist'


def test_batch_download_with_no_usable_albums_is_rejected(client, stub_backend):
    r = client.post("/api/discography/download", json={
        'url': 'https://a.bandcamp.com/album/x',
        'download_type': 'audio', 'format': 'mp3',
        'albums': [{'url': 'https://example.com/nope'}],
    })
    assert r.status_code == 422
    assert stub_backend == []


def test_batch_download_respects_the_album_cap(client, stub_backend, monkeypatch):
    monkeypatch.setattr(disco.settings, "MAX_DISCOGRAPHY_ALBUMS", 2)
    r = client.post("/api/discography/download", json={
        'url': 'https://a.bandcamp.com/album/x',
        'download_type': 'audio', 'format': 'mp3',
        'albums': [{'url': f'https://a.bandcamp.com/album/{i}'} for i in range(5)],
    })
    assert r.status_code == 400
    assert stub_backend == []


def test_batch_status_filters_unknown_and_invalid_ids(client, monkeypatch):
    from backend import app as app_mod

    known = 'ec1f0d4c-3a4e-4a4c-9f2f-2a3c1e6d5b7a'
    missing = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    monkeypatch.setattr(
        app_mod, "get_download_status",
        lambda did: {'id': did, 'status': 'downloading'} if did == known else None,
    )

    r = client.get(f"/api/downloads/status?ids={known},{missing},not-a-uuid")
    assert r.status_code == 200
    assert list(r.json()) == [known]


def test_batch_status_rejects_oversized_id_lists(client, monkeypatch):
    from backend import app as app_mod

    monkeypatch.setattr(app_mod.settings, "MAX_DISCOGRAPHY_ALBUMS", 2)
    ids = ','.join(['ec1f0d4c-3a4e-4a4c-9f2f-2a3c1e6d5b7a'] * 3)
    assert client.get(f"/api/downloads/status?ids={ids}").status_code == 400


# ── deferred album metadata in the queue ──────────────────────────────────────

def test_pending_metadata_is_resolved_and_row_refreshed(monkeypatch):
    from backend import queue_manager as qm

    written = {}

    async def fake_extract(url):
        return {
            'is_playlist': True, 'title': 'Real Album Title', 'artist': '',
            'album': 'Real Album Title', 'track_count': 9, 'platform': 'bandcamp',
        }

    monkeypatch.setattr(qm, "extract_metadata", fake_extract)
    monkeypatch.setattr(
        qm, "update_download_metadata",
        lambda _id, meta: written.update(meta),
    )

    placeholder = {'artist': 'Listing Artist', 'platform': 'bandcamp', 'needs_resolve': True}
    resolved = asyncio.run(
        qm._resolve_pending_metadata("id-1", "https://a.bandcamp.com/album/x", placeholder)
    )

    assert resolved['title'] == 'Real Album Title'
    assert resolved['track_count'] == 9
    # The album page didn't name the artist — keep the one the listing gave us.
    assert resolved['artist'] == 'Listing Artist'
    assert written['title'] == 'Real Album Title'


def test_pending_metadata_failure_raises(monkeypatch):
    from backend import queue_manager as qm

    async def fake_extract(url):
        return {'error': 'Video unavailable'}

    monkeypatch.setattr(qm, "extract_metadata", fake_extract)
    monkeypatch.setattr(qm, "update_download_metadata", lambda *a, **k: None)

    with pytest.raises(RuntimeError, match="Video unavailable"):
        asyncio.run(
            qm._resolve_pending_metadata("id-2", "https://a.bandcamp.com/album/x", {})
        )
