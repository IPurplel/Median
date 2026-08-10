"""Tests for reading Spotify links without an API key.

The entity fixtures below are trimmed copies of what
``open.spotify.com/embed/{kind}/{id}`` actually served, including its quirks:
albums leave `artists` null and put the name in `subtitle`, durations are
milliseconds, cover art hides under `visualIdentity`, and multiple artists are
joined with a non-breaking space. Everything here is offline. Run with:

    py -3 -m pytest backend/tests/test_spotify.py
"""
import json

import pytest

from backend import spotify


# ── fixtures, shaped exactly like the live payloads ──────────────────────────

TRACK = {
    'type': 'track',
    'name': 'Never Gonna Give You Up',
    'title': 'Never Gonna Give You Up',
    'uri': 'spotify:track:4cOdK2wGLETKBW3PvgPWqT',
    'artists': [{'name': 'Rick Astley', 'uri': 'spotify:artist:0gxyHStUsqpMadRV0Di1Qt'}],
    'releaseDate': {'isoString': '1987-11-12T00:00:00Z'},
    'duration': 213573,
    'visualIdentity': {'image': [
        {'url': 'https://image-cdn-fa.spotifycdn.com/image/ab67616d0000485115ebbe', 'maxWidth': 64},
        {'url': 'https://image-cdn-fa.spotifycdn.com/image/ab67616d00001e0215ebbe', 'maxWidth': 300},
    ]},
}

ALBUM = {
    'type': 'album',
    'name': 'Random Access Memories',
    'subtitle': 'Daft Punk',
    'artists': None,           # albums really do leave this null
    'releaseDate': None,       # ... and this
    'duration': 0,
    'visualIdentity': {'image': [
        {'url': 'https://image-cdn-fa.spotifycdn.com/image/ab67616d00001e029b9b36', 'maxWidth': 300},
    ]},
    'trackList': [
        {'uri': 'spotify:track:0dEIca2nhcxDUV8C5QkPYb',
         'title': 'Give Life Back to Music', 'subtitle': 'Daft Punk', 'duration': 275386},
        {'uri': 'spotify:track:3ctALmweZBapfBdFiIVpji',
         'title': 'The Game of Love', 'subtitle': 'Daft Punk', 'duration': 322415},
        {'uri': 'spotify:track:2cGxRwrMyEAp8dEbuZaVv6',
         # Spotify joins co-artists with a comma + NBSP
         'title': 'Instant Crush', 'subtitle': 'Daft Punk,\xa0Julian Casablancas',
         'duration': 337560},
    ],
}

PLAYLIST = {
    'type': 'playlist',
    'name': "Today's Top Hits",
    'subtitle': 'Spotify',        # the owner, not an artist
    'trackList': [
        {'uri': 'spotify:track:a', 'title': 'petal', 'subtitle': 'Ariana Grande', 'duration': 184248},
        {'uri': 'spotify:track:b', 'title': 'Anxiety', 'subtitle': 'Doechii', 'duration': 205000},
    ],
}


# ── URL parsing ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ('https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT',
     ('track', '4cOdK2wGLETKBW3PvgPWqT')),
    ('https://open.spotify.com/album/4m2880jivSbbyEGAKfITCa',
     ('album', '4m2880jivSbbyEGAKfITCa')),
    # Spotify injects a locale segment when you copy a link from the web player
    ('https://open.spotify.com/intl-de/album/4m2880jivSbbyEGAKfITCa',
     ('album', '4m2880jivSbbyEGAKfITCa')),
    # ... and the desktop app copies a URI instead of a URL
    ('spotify:playlist:37i9dQZF1DXcBWIGoYBM5M',
     ('playlist', '37i9dQZF1DXcBWIGoYBM5M')),
    ('https://open.spotify.com/artist/4tZwfgrHOc3mvqYlEYSvVi?si=abc',
     ('artist', '4tZwfgrHOc3mvqYlEYSvVi')),
])
def test_every_link_form_is_understood(url, expected):
    assert spotify.parse_spotify_url(url) == expected


@pytest.mark.parametrize("url", [
    '', 'https://youtube.com/watch?v=abc', 'https://open.spotify.com/track/short',
    'https://notspotify.com/track/4cOdK2wGLETKBW3PvgPWqT',
])
def test_non_spotify_links_are_rejected(url):
    assert spotify.parse_spotify_url(url) is None


# ── a single track ───────────────────────────────────────────────────────────

def test_track_metadata():
    meta = spotify.to_metadata(TRACK, 'https://open.spotify.com/track/x')

    assert meta['is_playlist'] is False
    assert meta['platform'] == 'spotify'
    assert meta['title'] == 'Never Gonna Give You Up'
    assert meta['artist'] == 'Rick Astley'
    assert meta['year'] == '1987'
    assert meta['release_date'] == '19871112'
    # Spotify reports milliseconds; everything downstream counts in seconds
    assert meta['duration'] == 213
    # Nothing is downloadable from Spotify itself — the queue has to go find it
    assert meta['needs_yt_match'] is True


def test_cover_art_is_upgraded_past_the_300px_the_embed_offers():
    meta = spotify.to_metadata(TRACK, 'u')
    # Largest listed image wins, then the size code is swapped for the 640px one
    assert meta['thumbnail'] == 'https://i.scdn.co/image/ab67616d0000b27315ebbe'


def test_missing_cover_art_is_not_an_error():
    meta = spotify.to_metadata(dict(TRACK, visualIdentity=None), 'u')
    assert meta['thumbnail'] == ''


# ── an album ─────────────────────────────────────────────────────────────────

def test_album_metadata_and_tracklist():
    meta = spotify.to_metadata(ALBUM, 'https://open.spotify.com/album/x')

    assert meta['is_playlist'] is True
    assert meta['album'] == 'Random Access Memories'
    # `artists` is null on albums — the name has to come from `subtitle`
    assert meta['artist'] == 'Daft Punk'
    assert meta['track_count'] == 3
    assert meta['total_duration'] == 275 + 322 + 337

    first = meta['tracks'][0]
    assert first['index'] == 1
    assert first['title'] == 'Give Life Back to Music'
    assert first['duration'] == 275
    # Left for the YouTube matcher to fill in
    assert first['url'] == ''


def test_non_breaking_space_between_artists_is_normalized():
    meta = spotify.to_metadata(ALBUM, 'u')
    artist = meta['tracks'][2]['artist']
    assert '\xa0' not in artist
    assert artist == 'Daft Punk, Julian Casablancas'


def test_album_length_is_kept_separately_from_the_selection():
    """Unticking tracks must not make the tag read '5 of 3'."""
    meta = spotify.to_metadata(ALBUM, 'u')
    assert meta['album_track_count'] == 3

    picked = dict(meta, tracks=meta['tracks'][:1], track_count=1)
    assert picked['album_track_count'] == 3


def test_tracks_without_titles_are_dropped():
    entity = dict(ALBUM, trackList=ALBUM['trackList'] + [{'uri': 'x', 'title': ''}])
    assert spotify.to_metadata(entity, 'u')['track_count'] == 3


def test_overlong_playlists_are_capped_and_say_so(monkeypatch):
    monkeypatch.setattr(spotify.settings, 'MAX_SPOTIFY_PLAYLIST_TRACKS', 2)
    meta = spotify.to_metadata(ALBUM, 'u')
    assert meta['track_count'] == 2
    assert 'note' in meta


# ── a playlist ───────────────────────────────────────────────────────────────

def test_playlist_owner_is_not_treated_as_the_artist():
    """`subtitle` is 'Spotify' here — tagging every file with that is wrong."""
    meta = spotify.to_metadata(PLAYLIST, 'u')
    assert meta['artist'] == 'Various Artists'
    assert meta['tracks'][0]['artist'] == 'Ariana Grande'


def test_single_artist_playlist_keeps_that_artist():
    entity = dict(PLAYLIST, trackList=[
        dict(t, subtitle='Doechii') for t in PLAYLIST['trackList']
    ])
    assert spotify.to_metadata(entity, 'u')['artist'] == 'Doechii'


# ── reading the embed page ───────────────────────────────────────────────────

def _page(entity):
    blob = {'props': {'pageProps': {'state': {'data': {'entity': entity}}}}}
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(blob) + '</script></body></html>'
    )


def test_entity_is_pulled_out_of_the_page(monkeypatch):
    monkeypatch.setattr(spotify, '_fetch', lambda *a, **k: _page(TRACK))
    assert spotify.fetch_entity('track', 'x')['name'] == 'Never Gonna Give You Up'


def test_a_page_without_the_blob_fails_readably(monkeypatch):
    monkeypatch.setattr(spotify, '_fetch', lambda *a, **k: '<html>nope</html>')
    with pytest.raises(RuntimeError, match='region-locked|private|layout'):
        spotify.fetch_entity('track', 'x')


def test_an_artist_link_points_at_the_discography_picker(monkeypatch):
    monkeypatch.setattr(spotify, '_fetch', lambda *a, **k: _page(TRACK))
    with pytest.raises(RuntimeError, match='every album by this artist'):
        spotify.fetch_metadata('https://open.spotify.com/artist/4tZwfgrHOc3mvqYlEYSvVi')


def test_album_release_date_is_borrowed_from_its_first_track(monkeypatch):
    """Albums report `releaseDate: null`; their tracks carry the real date.

    Without this the files keep whatever year their YouTube upload claimed,
    and one album can end up dated two different ways.
    """
    monkeypatch.setattr(spotify, 'fetch_entity',
                        lambda kind, sid: TRACK if kind == 'track' else ALBUM)
    meta = spotify.fetch_metadata('https://open.spotify.com/album/4m2880jivSbbyEGAKfITCa')
    assert meta['year'] == '1987'
    assert meta['release_date'] == '19871112'


# ── discography, via MusicBrainz ─────────────────────────────────────────────

def test_release_group_urls_round_trip():
    mbid = '00054665-89fa-33d5-a8f0-1728ea8c32c3'
    url = spotify.release_group_url(mbid)
    assert spotify.parse_release_group_url(url) == mbid
    assert spotify.parse_release_group_url('https://example.com/x') == ''


def test_discography_drops_bootlegs_and_live_albums(monkeypatch):
    responses = {
        'artist': {'artists': [{'id': 'mbid-1'}]},
        'release-group': {'release-groups': [
            {'id': 'a', 'title': 'Homework', 'first-release-date': '1997-01-20',
             'secondary-types': []},
            {'id': 'b', 'title': 'Alive 1997', 'first-release-date': '1997-10-01',
             'secondary-types': ['Live']},
            {'id': 'c', 'title': 'Musique Vol. 1', 'first-release-date': '2006-03-29',
             'secondary-types': ['Compilation']},
            {'id': 'd', 'title': 'Discovery', 'first-release-date': '2001-03-12',
             'secondary-types': []},
        ]},
    }
    monkeypatch.setattr(spotify, '_mb_get', lambda path, params, **kw: responses[path])

    albums = spotify.artist_discography('Daft Punk')
    assert [a['title'] for a in albums] == ['Homework', 'Discovery']   # oldest first
    assert albums[0]['url'].endswith('/release-group/a')


def test_discography_of_an_unknown_artist_is_empty_not_an_error(monkeypatch):
    monkeypatch.setattr(spotify, '_mb_get', lambda path, params, **kw: {'artists': []})
    assert spotify.artist_discography('Nobody At All') == []
    assert spotify.artist_discography('') == []


def test_the_album_wins_over_its_anniversary_edition(monkeypatch):
    """A release group holds every edition — asking for Homework should not
    hand back the 31-track 25th Anniversary Edition."""
    def fake(path, params, **kw):
        return {'releases': [
            {'title': 'Homework (25th Anniversary Edition)', 'status': 'Official',
             'date': '2022-02-25', 'release-group': {'title': 'Homework'},
             'artist-credit': [{'artist': {'name': 'Daft Punk'}}],
             'media': [{'tracks': [{'title': f'T{i}', 'length': 200000}
                                   for i in range(31)]}]},
            {'title': 'Homework', 'status': 'Official', 'date': '1997-01-20',
             'release-group': {'title': 'Homework'},
             'artist-credit': [{'artist': {'name': 'Daft Punk'}}],
             'media': [{'tracks': [{'title': f'S{i}', 'length': 200000}
                                   for i in range(16)]}]},
        ]}
    monkeypatch.setattr(spotify, '_mb_get', fake)

    meta = spotify.release_group_metadata('mbid', 'https://musicbrainz.org/release-group/mbid')
    assert meta['album'] == 'Homework'
    assert meta['track_count'] == 16
    assert meta['artist'] == 'Daft Punk'
    assert meta['year'] == '1997'
    assert meta['needs_yt_match'] is True


def test_a_release_with_no_date_falls_back_to_the_groups(monkeypatch):
    monkeypatch.setattr(spotify, '_mb_get', lambda path, params, **kw: {'releases': [
        {'title': 'Homework', 'status': 'Official', 'date': '',
         'release-group': {'title': 'Homework', 'first-release-date': '1997-01-20'},
         'artist-credit': [{'artist': {'name': 'Daft Punk'}}],
         'media': [{'tracks': [{'title': 'Daftendirekt', 'length': 164000}]}]},
    ]})
    meta = spotify.release_group_metadata('mbid', 'u')
    assert meta['year'] == '1997'
    assert meta['tracks'][0]['duration'] == 164


def test_a_group_with_no_tracklist_fails_readably(monkeypatch):
    monkeypatch.setattr(spotify, '_mb_get', lambda path, params, **kw: {'releases': []})
    with pytest.raises(RuntimeError, match='no tracklist'):
        spotify.release_group_metadata('mbid', 'u')
