"""Tests for picking the right YouTube upload for a known recording.

The candidate lists here are the real results a `ytsearch5:` returned, because
the interesting failure modes are the ones YouTube actually produces: the top
hit being a radio edit when the album version was asked for, indexed videos
that turn out to be undownloadable, and live takes charting alongside studio
ones. Nothing here touches the network. Run with:

    py -3 -m pytest backend/tests/test_yt_match.py
"""
import asyncio

import pytest

from backend.utils import yt_match
from backend.utils.yt_match import CONFIDENT, Match, score_candidate


def _c(title, duration, channel, url='https://youtu.be/x'):
    return {'title': title, 'duration': duration, 'channel': channel, 'url': url}


# The genuine search results for "Daft Punk - Get Lucky". Note the album
# version (6:09) is third, behind two copies of the 4:09 single edit.
GET_LUCKY = [
    _c('Daft Punk - Get Lucky (Official Audio) ft. Pharrell Williams, Nile Rodgers',
       249, 'Daft Punk', 'https://youtu.be/official'),
    _c('Daft Punk - Get Lucky (Official Video) feat. Pharrell Williams and Nile Rodgers',
       248, 'convar HUN', 'https://youtu.be/reupload'),
    _c('Daft Punk - Get Lucky (Feat. Pharrell Williams)',
       369, 'EXPO STORY', 'https://youtu.be/album'),
    _c('Daft Punk - Get Lucky (Lyrics) ft. Pharrell Williams, Nile Rodgers',
       246, '7clouds', 'https://youtu.be/lyrics'),
    _c('Stevie Wonder & Daft Punk & Pharrell Williams - Get Lucky ( Medley ) Live Grammy Performance',
       340, 'Radyo Burada', 'https://youtu.be/live'),
]


def _best(candidates, title, artist, duration):
    scored = [(score_candidate(c, title, artist, duration), c) for c in candidates]
    scored = [p for p in scored if p[0] > 0]
    return max(scored, key=lambda p: p[0])[1] if scored else None


# ── duration is the deciding signal ──────────────────────────────────────────

def test_album_version_beats_the_official_radio_edit():
    """Ranking by how official a result looks would return the wrong length."""
    assert _best(GET_LUCKY, 'Get Lucky', 'Daft Punk', 369)['url'] == 'https://youtu.be/album'


def test_and_the_radio_edit_wins_when_that_is_what_was_asked_for():
    assert _best(GET_LUCKY, 'Get Lucky', 'Daft Punk', 249)['url'] == 'https://youtu.be/official'


def test_wrong_length_is_disqualifying_not_merely_penalised():
    for wanted, url in ((369, 'https://youtu.be/official'), (249, 'https://youtu.be/album')):
        candidate = next(c for c in GET_LUCKY if c['url'] == url)
        assert score_candidate(candidate, 'Get Lucky', 'Daft Punk', wanted) == 0.0


def test_a_live_medley_never_wins():
    live = next(c for c in GET_LUCKY if c['url'] == 'https://youtu.be/live')
    # Even at its own length, "Live" and "Medley" rule it out of contention
    assert score_candidate(live, 'Get Lucky', 'Daft Punk', 340) < CONFIDENT


def test_a_song_actually_called_live_is_not_penalised_for_it():
    wanted = _c('Alive 2007 (Live)', 200, 'Daft Punk - Topic')
    assert score_candidate(wanted, 'Alive 2007 (Live)', 'Daft Punk', 200) >= CONFIDENT


# ── channel signals ──────────────────────────────────────────────────────────

def test_topic_channels_outrank_equally_good_reuploads():
    """'Artist - Topic' is the label's own upload: a clean master, no video."""
    topic = _c('Instant Crush', 337, 'Daft Punk - Topic', 'https://youtu.be/topic')
    fan = _c('Instant Crush', 337, 'SomeFanChannel', 'https://youtu.be/fan')
    picked = _best([fan, topic], 'Instant Crush', 'Daft Punk', 337)
    assert picked['url'] == 'https://youtu.be/topic'


def test_the_artist_may_appear_in_the_channel_instead_of_the_title():
    only_channel = _c('Get Lucky', 369, 'Daft Punk')
    assert score_candidate(only_channel, 'Get Lucky', 'Daft Punk', 369) >= CONFIDENT


# ── disqualifications ────────────────────────────────────────────────────────

def test_live_streams_are_never_songs():
    stream = dict(_c('Get Lucky', 369, 'Daft Punk'), live_status='is_live')
    assert score_candidate(stream, 'Get Lucky', 'Daft Punk', 369) == 0.0


def test_a_candidate_with_no_title_or_no_link_scores_zero():
    assert score_candidate({'title': '', 'url': 'x'}, 'Get Lucky', 'Daft Punk') == 0.0
    assert score_candidate({'title': 'Get Lucky'}, 'Get Lucky', 'Daft Punk') == 0.0


def test_an_unrelated_song_does_not_pass_as_confident():
    other = _c('Around the World', 428, 'Daft Punk')
    assert score_candidate(other, 'Get Lucky', 'Daft Punk', 0) < CONFIDENT


# ── choosing, including availability ─────────────────────────────────────────

def _verdicts(available):
    """Adapt a url -> bool predicate to the Availability-returning probe."""
    return lambda url: (yt_match.Availability.AVAILABLE if available(url)
                        else yt_match.Availability.CONTENT_UNAVAILABLE)


def test_an_indexed_but_undownloadable_result_is_skipped(monkeypatch):
    """Search lists region-locked and removed videos; two of the first three
    tracks of a real album hit this, so the pick has to be confirmed."""
    monkeypatch.setattr(yt_match, '_search', lambda q, n: GET_LUCKY)
    dead = {'https://youtu.be/album'}
    monkeypatch.setattr(yt_match, '_check_availability', _verdicts(lambda url: url not in dead))

    match = yt_match.match_track_sync('Get Lucky', 'Daft Punk', 369)
    # The only 6:09 candidate is unavailable, so it falls back to ignoring
    # duration — and flags the result as shaky rather than passing it off.
    assert match is not None
    assert match.url != 'https://youtu.be/album'
    assert not match.is_confident


def test_the_top_pick_is_used_when_it_works(monkeypatch):
    monkeypatch.setattr(yt_match, '_search', lambda q, n: GET_LUCKY)
    monkeypatch.setattr(yt_match, '_check_availability', _verdicts(lambda url: True))

    match = yt_match.match_track_sync('Get Lucky', 'Daft Punk', 369)
    assert match.url == 'https://youtu.be/album'
    assert match.is_confident


def test_runner_ups_come_along_as_fallbacks(monkeypatch):
    """They cost nothing extra — the search already returned them — and give
    the downloader somewhere to go when a video 403s stubbornly."""
    monkeypatch.setattr(yt_match, '_search', lambda q, n: GET_LUCKY)
    monkeypatch.setattr(yt_match, '_check_availability', _verdicts(lambda url: True))

    match = yt_match.match_track_sync('Get Lucky', 'Daft Punk', 369)
    assert match.url == 'https://youtu.be/album'
    assert match.alternatives                       # there are some
    assert match.url not in match.alternatives      # not the one already chosen
    assert len(match.alternatives) <= yt_match.settings.YT_MATCH_FALLBACKS


def test_fallback_list_is_capped(monkeypatch):
    monkeypatch.setattr(yt_match, '_search', lambda q, n: GET_LUCKY)
    monkeypatch.setattr(yt_match, '_check_availability', _verdicts(lambda url: True))
    monkeypatch.setattr(yt_match.settings, 'YT_MATCH_FALLBACKS', 1)

    match = yt_match.match_track_sync('Get Lucky', 'Daft Punk', 249)
    assert len(match.alternatives) == 1


def test_fallbacks_are_ranked_below_the_pick(monkeypatch):
    monkeypatch.setattr(yt_match, '_search', lambda q, n: GET_LUCKY)
    # Best candidate is dead, so the pick drops to a lower-ranked one; the
    # alternatives must be the ones below *that*, not above it.
    monkeypatch.setattr(yt_match, '_check_availability',
                        _verdicts(lambda url: url != 'https://youtu.be/official'))
    monkeypatch.setattr(yt_match.settings, 'YT_MATCH_FALLBACKS', 5)

    match = yt_match.match_track_sync('Get Lucky', 'Daft Punk', 249)
    assert match.url == 'https://youtu.be/reupload'
    assert 'https://youtu.be/official' not in match.alternatives


def test_nothing_downloadable_means_no_match(monkeypatch):
    monkeypatch.setattr(yt_match, '_search', lambda q, n: GET_LUCKY)
    monkeypatch.setattr(yt_match, '_check_availability', _verdicts(lambda url: False))
    assert yt_match.match_track_sync('Get Lucky', 'Daft Punk', 369) is None


def test_an_empty_search_means_no_match(monkeypatch):
    monkeypatch.setattr(yt_match, '_search', lambda q, n: [])
    assert yt_match.match_track_sync('Zzqxwv', 'Nobody', 200) is None
    assert yt_match.match_track_sync('', '', 0) is None


# ── the Match object ─────────────────────────────────────────────────────────

def test_confidence_threshold():
    assert Match('u', CONFIDENT).is_confident
    assert not Match('u', CONFIDENT - 0.01).is_confident


def test_match_all_keeps_results_aligned_with_tracks(monkeypatch):
    """A skipped track must not shift every later match onto the wrong song."""
    tracks = [
        {'title': 'One', 'artist': 'A', 'duration': 100},
        {'title': 'Two', 'artist': 'A', 'duration': 200},
        {'title': 'Three', 'artist': 'A', 'duration': 300},
    ]

    def fake(title, artist, duration=0):
        return None if title == 'Two' else Match(f'url-{title}', 0.9)
    monkeypatch.setattr(yt_match, 'match_track_sync', fake)

    results = asyncio.run(yt_match.match_all(tracks))
    assert len(results) == len(tracks)
    assert results[0].url == 'url-One'
    assert results[1] is None
    assert results[2].url == 'url-Three'


def test_match_all_survives_a_track_that_raises(monkeypatch):
    def fake(title, artist, duration=0):
        if title == 'Two':
            raise RuntimeError('search exploded')
        return Match(f'url-{title}', 0.9)
    monkeypatch.setattr(yt_match, 'match_track_sync', fake)

    results = asyncio.run(yt_match.match_all([
        {'title': 'One', 'artist': 'A', 'duration': 100},
        {'title': 'Two', 'artist': 'A', 'duration': 200},
    ]))
    assert results[0].url == 'url-One'
    assert results[1] is None


# ── infrastructure failures are not "not found" (BE-001) ─────────────────────

def _probe_error(url, verdict=None):
    verdict = verdict or yt_match.Availability.EXTRACTOR_FAILURE
    return yt_match.CandidateProbeError(url, verdict, 'boom')


def test_classifier_separates_gone_videos_from_infrastructure():
    c = yt_match._classify_probe_error
    assert c(Exception('ERROR: [youtube] abc: Video unavailable')) \
        is yt_match.Availability.CONTENT_UNAVAILABLE
    assert c(Exception('ERROR: [youtube] abc: Private video')) \
        is yt_match.Availability.CONTENT_UNAVAILABLE
    assert c(Exception('<urlopen error timed out>')) \
        is yt_match.Availability.NETWORK_FAILURE
    assert c(Exception('Signature extraction failed: no JS runtime')) \
        is yt_match.Availability.EXTRACTOR_FAILURE


def test_a_broken_probe_on_every_candidate_raises_instead_of_none(monkeypatch):
    monkeypatch.setattr(yt_match, '_search', lambda q, n: GET_LUCKY)

    def broken(url):
        raise _probe_error(url)
    monkeypatch.setattr(yt_match, '_check_availability', broken)

    with pytest.raises(yt_match.CandidateProbeError):
        yt_match.match_track_sync('Get Lucky', 'Daft Punk', 369)


def test_one_flaky_probe_still_lets_the_next_candidate_win(monkeypatch):
    monkeypatch.setattr(yt_match, '_search', lambda q, n: GET_LUCKY)

    def flaky(url):
        if url == 'https://youtu.be/album':
            raise _probe_error(url, yt_match.Availability.NETWORK_FAILURE)
        return yt_match.Availability.AVAILABLE
    monkeypatch.setattr(yt_match, '_check_availability', flaky)

    match = yt_match.match_track_sync('Get Lucky', 'Daft Punk', 369)
    assert match is not None and match.url != 'https://youtu.be/album'


def test_a_failed_search_raises_instead_of_reporting_no_results(monkeypatch):
    import yt_dlp

    class Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, *a, **k):
            raise yt_dlp.utils.DownloadError('Unable to download API page: timed out')
    monkeypatch.setattr(yt_dlp, 'YoutubeDL', Boom)

    with pytest.raises(yt_match.CandidateProbeError) as info:
        yt_match.match_track_sync('Get Lucky', 'Daft Punk', 369)
    assert info.value.verdict is yt_match.Availability.NETWORK_FAILURE


def test_match_all_reports_probe_failures_separately(monkeypatch):
    import asyncio

    def fake(title, artist, duration):
        if title == 'Broken':
            raise _probe_error('u')
        return None
    monkeypatch.setattr(yt_match, 'match_track_sync', fake)

    failures = []
    tracks = [{'title': 'Broken'}, {'title': 'Missing'}]
    result = asyncio.run(yt_match.match_all(tracks, failures=failures))
    assert result == [None, None]
    assert [t['title'] for t, _ in failures] == ['Broken']
