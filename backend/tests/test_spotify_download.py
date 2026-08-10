"""Tests for downloading an album whose tracks come from unrelated sources.

Every other platform hands yt-dlp one playlist URL and lets it walk the
entries. A Spotify album can't work that way: each track has been matched to a
different YouTube upload, so they are fetched one at a time and tagged from the
Spotify tracklist rather than from whatever the video was called.

No network and no yt-dlp — a stub stands in for the download itself. Run with:

    py -3 -m pytest backend/tests/test_spotify_download.py
"""
import asyncio
from pathlib import Path

import pytest

from backend import downloader
from backend.queue_manager import _resolve_spotify_matches
from backend.utils.yt_match import Match


# ── stub yt-dlp ──────────────────────────────────────────────────────────────

class _FakeYDL:
    """Writes a file where the real thing would, or refuses to.

    `dead` URLs are permanently gone. `flaky` URLs fail the given number of
    times and then succeed, standing in for YouTube's sporadic 403 on a
    perfectly healthy video.
    """

    dead: set = set()
    flaky: dict = {}
    attempts: list = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        url = urls[0]
        _FakeYDL.attempts.append(url)
        if url in _FakeYDL.dead:
            raise RuntimeError(f"ERROR: [youtube] {url}: Video unavailable")
        if _FakeYDL.flaky.get(url, 0) > 0:
            _FakeYDL.flaky[url] -= 1
            raise RuntimeError(
                'ERROR: unable to download video data: HTTP Error 403: Forbidden')
        target = Path(self.opts['outtmpl'].replace('.%(ext)s', '.mp3'))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'audio')


@pytest.fixture
def fake_ydl(monkeypatch):
    # The downloader imports yt_dlp inside the function, so it may not be
    # loaded yet — import it here so there is something to patch.
    import yt_dlp

    _FakeYDL.dead = set()
    _FakeYDL.flaky = {}
    _FakeYDL.attempts = []
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', _FakeYDL)
    # No real sleeping between retries
    monkeypatch.setattr(downloader.asyncio, 'sleep', _no_sleep)
    return _FakeYDL


async def _no_sleep(_seconds):
    return None


def _tracks():
    return [
        {'index': 2, 'title': 'The Game of Love', 'artist': 'Daft Punk',
         'duration': 322, 'url': 'https://youtu.be/two'},
        {'index': 5, 'title': 'Instant Crush', 'artist': 'Daft Punk',
         'duration': 337, 'url': 'https://youtu.be/five'},
    ]


def _run(tracks, folder, warnings=None):
    async def progress(pct, message='', warning=None, speed=None, eta=None):
        if warning is not None and warnings is not None:
            warnings.append(warning)

    return asyncio.run(downloader._download_each_track(
        tracks, Path(folder), 'audio', 'mp3', '192', progress
    ))


# ── filenames and ordering ───────────────────────────────────────────────────

def test_files_are_named_from_the_tracklist_not_the_video_title(fake_ydl, tmp_path):
    """yt-dlp's %(title)s would write "Daft Punk - Song (Official Video)"."""
    results = _run(_tracks(), tmp_path)

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ['002 - The Game of Love.mp3', '005 - Instant Crush.mp3']
    assert len(results) == 2


def test_numbering_keeps_the_original_album_positions(fake_ydl, tmp_path):
    """Picking tracks 2 and 5 must not renumber them 1 and 2 — they still have
    to sort alongside the rest of the album."""
    _run(_tracks(), tmp_path)
    assert (tmp_path / '002 - The Game of Love.mp3').exists()
    assert (tmp_path / '005 - Instant Crush.mp3').exists()


def test_each_track_is_fetched_from_its_own_url(fake_ydl, tmp_path):
    _run(_tracks(), tmp_path)
    assert fake_ydl.attempts == ['https://youtu.be/two', 'https://youtu.be/five']


def test_illegal_filename_characters_are_stripped(fake_ydl, tmp_path):
    tracks = [{'index': 1, 'title': 'AC/DC: Back?', 'artist': 'X',
               'url': 'https://youtu.be/a'}]
    _run(tracks, tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ['001 - ACDC Back.mp3']


# ── failures ─────────────────────────────────────────────────────────────────

def test_a_dead_video_is_not_pointlessly_retried(fake_ydl, tmp_path):
    """A removed video will still be removed in two seconds — no fresh
    signature can bring it back, so retrying it only wastes time."""
    fake_ydl.dead = {'https://youtu.be/two'}
    warnings = []
    results = _run(_tracks(), tmp_path, warnings)

    assert fake_ydl.attempts.count('https://youtu.be/two') == 1
    assert len(results) == 1
    assert results[0][0]['title'] == 'Instant Crush'
    assert any('The Game of Love' in w for w in warnings)


def test_a_transient_403_is_retried_with_a_fresh_extraction(fake_ydl, tmp_path):
    """The observed failure mode: YouTube 403s a healthy video because the
    signed URL it just issued is rejected. A second extraction fixes it."""
    fake_ydl.flaky = {'https://youtu.be/two': 1}
    warnings = []
    results = _run(_tracks(), tmp_path, warnings)

    assert fake_ydl.attempts.count('https://youtu.be/two') == 2
    assert len(results) == 2                  # nothing lost
    assert not warnings                       # and nothing to report
    assert (tmp_path / '002 - The Game of Love.mp3').exists()


def test_a_stubborn_source_falls_back_to_the_runner_up(fake_ydl, tmp_path):
    tracks = _tracks()[:1]
    tracks[0]['url_alternatives'] = ['https://youtu.be/alt1', 'https://youtu.be/alt2']
    fake_ydl.dead = {'https://youtu.be/two'}
    results = _run(tracks, tmp_path)

    assert len(results) == 1
    assert fake_ydl.attempts[0] == 'https://youtu.be/two'
    assert fake_ydl.attempts[1] == 'https://youtu.be/alt1'
    assert (tmp_path / '002 - The Game of Love.mp3').exists()


def test_fallbacks_are_exhausted_before_giving_up(fake_ydl, tmp_path):
    tracks = _tracks()[:1]
    tracks[0]['url_alternatives'] = ['https://youtu.be/alt1', 'https://youtu.be/alt2']
    fake_ydl.dead = {'https://youtu.be/two', 'https://youtu.be/alt1'}
    results = _run(tracks, tmp_path)

    assert len(results) == 1
    assert fake_ydl.attempts[-1] == 'https://youtu.be/alt2'


@pytest.mark.parametrize("message", [
    'ERROR: [youtube] abc: Video unavailable',
    'ERROR: [youtube] abc: This video is not available',
    'ERROR: [youtube] abc: This video is unavailable',
    'ERROR: [youtube] abc: Private video. Sign in if you have been granted access',
])
def test_every_wording_of_gone_is_recognised(message):
    """Caught live: the marker list matched "Video unavailable" but not
    "This video is not available", so dead videos were retried pointlessly."""
    from backend.queue_manager import _is_permanent_error
    assert downloader._is_permanently_gone(RuntimeError(message)), message
    # The queue makes the same judgement from the same text
    assert _is_permanent_error(RuntimeError(message)), message


def test_a_transient_403_is_not_mistaken_for_a_dead_video():
    err = RuntimeError('ERROR: unable to download video data: HTTP Error 403: Forbidden')
    assert not downloader._is_permanently_gone(err)


def test_the_real_error_survives_so_the_queue_can_read_it(fake_ydl, tmp_path):
    """queue_manager decides from the message whether a retry is worth it, so a
    generic 'no file produced' would make dead videos look transient."""
    fake_ydl.dead = {'https://youtu.be/only'}
    with pytest.raises(Exception, match='Video unavailable'):
        asyncio.run(downloader._fetch_with_fallback(
            ['https://youtu.be/only'],
            {'outtmpl': str(tmp_path / 'x.%(ext)s')}, 'label'))


def test_one_failure_does_not_abort_the_rest_of_the_album(fake_ydl, tmp_path):
    fake_ydl.dead = {'https://youtu.be/two'}
    _run(_tracks(), tmp_path)
    assert (tmp_path / '005 - Instant Crush.mp3').exists()


def test_a_track_with_no_match_is_reported_not_silently_missing(fake_ydl, tmp_path):
    tracks = _tracks()
    tracks[0]['url'] = ''
    warnings = []
    results = _run(tracks, tmp_path, warnings)

    assert len(results) == 1
    assert any('No source found' in w and 'The Game of Love' in w for w in warnings)
    assert fake_ydl.attempts == ['https://youtu.be/five']


# ── resolving Spotify metadata to real sources ───────────────────────────────

def _resolve(meta, warnings):
    async def progress(pct, message='', warning=None, speed=None, eta=None):
        if warning is not None:
            warnings.append(warning)
    return asyncio.run(_resolve_spotify_matches(meta, meta['url'], progress))


def _album_meta():
    return {
        'is_playlist': True, 'platform': 'spotify', 'album': 'Random Access Memories',
        'artist': 'Daft Punk', 'url': 'https://open.spotify.com/album/x',
        'track_count': 2, 'album_track_count': 13, 'tracks': [
            {'index': 1, 'title': 'One', 'artist': 'Daft Punk', 'duration': 100},
            {'index': 2, 'title': 'Two', 'artist': 'Daft Punk', 'duration': 200},
        ],
    }


def test_resolving_points_each_track_at_a_real_source(monkeypatch):
    import backend.utils.yt_match as ytm
    monkeypatch.setattr(
        ytm, 'match_track_sync',
        lambda title, artist, duration=0: Match(f'https://youtu.be/{title}', 0.9),
    )
    warnings = []
    meta, url = _resolve(_album_meta(), warnings)

    assert [t['url'] for t in meta['tracks']] == [
        'https://youtu.be/One', 'https://youtu.be/Two']
    # Tells the downloader these are separate sources, not one playlist
    assert meta['per_track_urls'] is True
    assert not warnings


def test_unmatched_tracks_are_dropped_with_a_note(monkeypatch):
    import backend.utils.yt_match as ytm
    monkeypatch.setattr(
        ytm, 'match_track_sync',
        lambda title, artist, duration=0: (
            None if title == 'Two' else Match('https://youtu.be/one', 0.9)
        ),
    )
    warnings = []
    meta, _ = _resolve(_album_meta(), warnings)

    assert meta['track_count'] == 1
    assert any("couldn't be found" in w and 'Two' in w for w in warnings)


def test_a_shaky_match_is_downloaded_but_flagged(monkeypatch):
    """The user asked for the best match plus a warning, not a silent guess."""
    import backend.utils.yt_match as ytm
    monkeypatch.setattr(
        ytm, 'match_track_sync',
        lambda title, artist, duration=0: Match(
            'https://youtu.be/live', 0.3, title=f'{title} (Live in Tokyo)'),
    )
    warnings = []
    meta, _ = _resolve(_album_meta(), warnings)

    assert meta['track_count'] == 2          # still downloaded
    assert any('wrong version' in w for w in warnings)


def test_an_album_with_nothing_findable_fails_loudly(monkeypatch):
    import backend.utils.yt_match as ytm
    monkeypatch.setattr(ytm, 'match_track_sync',
                        lambda title, artist, duration=0: None)
    with pytest.raises(RuntimeError, match='copy-protected'):
        _resolve(_album_meta(), [])


def test_a_single_track_resolves_to_its_download_url(monkeypatch):
    import backend.utils.yt_match as ytm
    monkeypatch.setattr(
        ytm, 'match_track_sync',
        lambda title, artist, duration=0: Match('https://youtu.be/single', 0.9),
    )
    meta = {'is_playlist': False, 'platform': 'spotify', 'title': 'Never Gonna Give You Up',
            'artist': 'Rick Astley', 'duration': 213,
            'url': 'https://open.spotify.com/track/x'}
    resolved, url = _resolve(meta, [])

    # The Spotify URL is unusable for downloading — it must be replaced
    assert url == 'https://youtu.be/single'
    assert resolved['title'] == 'Never Gonna Give You Up'


def test_a_single_track_that_cannot_be_found_fails(monkeypatch):
    import backend.utils.yt_match as ytm
    monkeypatch.setattr(ytm, 'match_track_sync',
                        lambda title, artist, duration=0: None)
    meta = {'is_playlist': False, 'platform': 'spotify', 'title': 'Zzqxwv',
            'artist': 'Nobody', 'duration': 200, 'url': 'https://open.spotify.com/track/x'}
    with pytest.raises(RuntimeError, match='copy-protected'):
        _resolve(meta, [])
