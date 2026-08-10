"""Tests for the "time remaining" estimate.

A wrong estimate is worse than none — a countdown that jumps around, ticks
upward, or survives past the end of the job makes the whole progress display
untrustworthy. These pin down the cases where it must stay quiet. Run with:

    py -3 -m pytest backend/tests/test_eta.py
"""
import pytest

from backend.utils.eta import JobEta, TrackWeights, format_eta, normalize_ytdlp_eta


# ── formatting ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("seconds,expected", [
    (0, 'a few seconds'),
    (3, 'a few seconds'),        # sub-5s countdowns only flicker
    (5, '5s'),
    (45, '45s'),
    (60, '1m 00s'),
    (90, '1m 30s'),
    (599, '9m 59s'),
    (3600, '1h 00m'),
    (7845, '2h 10m'),
])
def test_durations_read_naturally(seconds, expected):
    assert format_eta(seconds) == expected


@pytest.mark.parametrize("value", [None, -1, 'nonsense', object()])
def test_unknowable_durations_say_nothing(value):
    assert format_eta(value) == ''


# ── yt-dlp's own figures ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (42, '42s'),
    (125, '2m 05s'),
    ('00:42', '42s'),
    ('02:05', '2m 05s'),
    ('01:02:05', '1h 02m'),
])
def test_ytdlp_eta_is_converted(raw, expected):
    assert normalize_ytdlp_eta(raw) == expected


@pytest.mark.parametrize("raw", [None, '', 'Unknown', 'N/A', '--:--', 'garbage'])
def test_ytdlp_non_answers_are_dropped(raw):
    assert normalize_ytdlp_eta(raw) == ''


# ── the job-level estimate ───────────────────────────────────────────────────

def _tracker(monkeypatch, clock):
    """A JobEta driven by a fake clock so tests don't sleep."""
    import backend.utils.eta as eta_mod
    monkeypatch.setattr(eta_mod.time, 'monotonic', lambda: clock[0])
    return JobEta()


def test_no_estimate_until_there_is_evidence(monkeypatch):
    """The first moments are all connection setup — extrapolating from them
    produces a wild number that immediately corrects itself."""
    clock = [0.0]
    eta = _tracker(monkeypatch, clock)

    clock[0] = 1.0
    assert eta.update(0.01) is None      # too little progress, too little time
    clock[0] = 2.0
    assert eta.update(0.30) is None      # good progress, but only 2s of it


def test_estimates_once_the_sample_is_meaningful(monkeypatch):
    clock = [0.0]
    eta = _tracker(monkeypatch, clock)

    clock[0] = 10.0
    remaining = eta.update(0.5)          # half done in 10s
    assert remaining == pytest.approx(10.0, abs=0.1)


def test_the_estimate_counts_down_as_work_proceeds(monkeypatch):
    clock = [0.0]
    eta = _tracker(monkeypatch, clock)

    clock[0] = 10.0
    first = eta.update(0.25)
    clock[0] = 20.0
    second = eta.update(0.50)
    clock[0] = 30.0
    third = eta.update(0.75)

    assert first > second > third


def test_a_slower_stretch_is_smoothed_not_lurched(monkeypatch):
    """One slow track shouldn't double the displayed estimate on its own."""
    clock = [0.0]
    eta = _tracker(monkeypatch, clock)

    clock[0] = 10.0
    eta.update(0.5)                       # settled at ~10s remaining
    clock[0] = 40.0
    jumped = eta.update(0.55)             # a very slow stretch

    raw = 40.0 * 0.45 / 0.55              # what an unsmoothed estimate would say
    assert jumped < raw                   # damped
    assert jumped > 10.0                  # but it did respond


def _applied_share(monkeypatch, clock, later_time, later_fraction):
    """How much of the raw correction the tracker actually took on board."""
    clock[0] = 0.0                        # each measurement starts its own job
    eta = _tracker(monkeypatch, clock)
    clock[0] = 10.0
    eta.update(0.5)                       # settles at ~10s remaining
    before = eta._estimate

    clock[0] = later_time
    raw = later_time * (1 - later_fraction) / later_fraction
    after = eta.update(later_fraction)
    return (after - before) / (raw - before)


def test_rises_are_damped_harder_than_falls(monkeypatch):
    """Track sizes vary, so the raw figure jitters both ways. A countdown seen
    going up reads as a stall; one going down just reads as good news."""
    clock = [0.0]
    # t=20s having done 55% → ~16s left: worse than the 10s we thought
    rise_share = _applied_share(monkeypatch, clock, 20.0, 0.55)
    # t=11s having done 65% → ~6s left: better than we thought
    fall_share = _applied_share(monkeypatch, clock, 11.0, 0.65)

    assert 0 < rise_share < fall_share <= 1.0


def test_a_sustained_slowdown_still_gets_through(monkeypatch):
    """Damping rises must not mean pretending a genuine stall isn't happening."""
    clock = [0.0]
    eta = _tracker(monkeypatch, clock)
    clock[0] = 10.0
    start = eta.update(0.5)

    for _ in range(6):                    # progress crawls while time passes
        clock[0] += 20.0
        eta.update(min(0.99, eta._last_fraction + 0.01))

    assert eta._estimate > start * 2


def test_a_retry_restarts_the_estimate(monkeypatch):
    """A failed attempt rewinds the progress bar; timings from the abandoned
    attempt no longer describe the work that is left."""
    clock = [0.0]
    eta = _tracker(monkeypatch, clock)

    clock[0] = 10.0
    assert eta.update(0.6) is not None
    clock[0] = 12.0
    assert eta.update(0.0) is None        # rewound → estimate withdrawn
    clock[0] = 13.0
    assert eta.update(0.1) is None        # and rebuilding from the new start


def test_finishing_reports_zero_not_a_leftover(monkeypatch):
    clock = [0.0]
    eta = _tracker(monkeypatch, clock)
    clock[0] = 10.0
    eta.update(0.5)
    assert eta.update(1.0) == 0.0


def test_bad_input_never_raises(monkeypatch):
    clock = [0.0]
    eta = _tracker(monkeypatch, clock)
    assert eta.update(None) is None
    assert eta.update('nonsense') is None


def test_remaining_text_is_blank_while_unsure(monkeypatch):
    clock = [0.0]
    eta = _tracker(monkeypatch, clock)
    assert eta.remaining_text(0.01) == ''
    clock[0] = 20.0
    assert eta.remaining_text(0.5) == '20s'


# ── weighting tracks by length ───────────────────────────────────────────────

def test_long_tracks_count_for_more_than_short_ones():
    """Counting tracks equally makes the estimate lurch: a 28-second interlude
    is not the same amount of work as a 7-minute closer."""
    weights = TrackWeights([
        {'duration': 30}, {'duration': 270}, {'duration': 300},
    ])
    # Finishing the 30s opener is 5% of the album, not a third of it
    assert weights.fraction(1) == pytest.approx(0.05)
    assert weights.fraction(2) == pytest.approx(0.5)
    assert weights.fraction(3) == pytest.approx(1.0)


def test_progress_inside_a_track_is_scaled_by_its_share():
    weights = TrackWeights([{'duration': 100}, {'duration': 300}])
    assert weights.fraction(0, 0.5) == pytest.approx(0.125)   # half of 100/400
    assert weights.fraction(1, 0.5) == pytest.approx(0.625)   # 100/400 + half of 300/400


def test_missing_durations_fall_back_to_counting():
    for tracks in ([{}, {}, {}], [{'duration': 0}] * 3, [{'duration': None}] * 3):
        weights = TrackWeights(tracks)
        assert weights.fraction(1) == pytest.approx(1 / 3)


def test_weights_never_divide_by_zero_or_overrun():
    empty = TrackWeights([])
    assert empty.fraction(0) in (0.0, 1.0)
    weights = TrackWeights([{'duration': 100}])
    assert weights.fraction(5) == 1.0          # past the end
    assert weights.fraction(-1) == 0.0         # nonsense index
    assert weights.fraction(0, 9.0) == 1.0     # over-reported bytes


# ── reaching the user ────────────────────────────────────────────────────────
#
# The estimator was only ever half the job: the UI and the database column
# already existed, but nothing carried a figure between them. These cover the
# plumbing, which is where the feature was actually missing.

def _download_state(monkeypatch, tmp_path):
    """A queue-managed download whose progress callback we can drive."""
    from backend import db_models, queue_manager

    monkeypatch.setattr(db_models.settings, 'DATABASE_PATH', str(tmp_path / 'm.db'))
    db_models.init_db()
    download_id = queue_manager.create_download_record(
        url='https://example.com/x', platform='spotify', download_type='audio',
        fmt='mp3', bitrate='192', metadata={'title': 'T', 'artist': 'A'},
    )
    return queue_manager, download_id


def test_an_album_download_reports_a_countdown(monkeypatch, tmp_path):
    """Drives the real per-track loop with a stubbed yt-dlp and asserts a
    figure actually comes out — the plumbing, not just the arithmetic."""
    import asyncio
    import yt_dlp
    from backend import downloader

    clock = [0.0]
    import backend.utils.eta as eta_mod
    monkeypatch.setattr(eta_mod.time, 'monotonic', lambda: clock[0])

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            hook = (self.opts.get('progress_hooks') or [None])[0]
            if hook:
                # Time passes while bytes arrive, so there is something to
                # extrapolate from by the second track.
                clock[0] += 10.0
                hook({'status': 'downloading', 'downloaded_bytes': 500,
                      'total_bytes': 1000, '_speed_str': '1.00MiB/s'})
            out = self.opts['outtmpl'].replace('.%(ext)s', '.mp3')
            from pathlib import Path as _P
            _P(out).parent.mkdir(parents=True, exist_ok=True)
            _P(out).write_bytes(b'audio')

    monkeypatch.setattr(yt_dlp, 'YoutubeDL', FakeYDL)

    seen = []

    async def progress(pct, message='', warning=None, speed=None, eta=None):
        seen.append({'pct': pct, 'speed': speed, 'eta': eta})

    tracks = [
        {'index': i, 'title': f'Track {i}', 'artist': 'A',
         'url': f'https://youtu.be/{i}'} for i in range(1, 5)
    ]
    asyncio.run(downloader._download_each_track(
        tracks, tmp_path, 'audio', 'mp3', '192', progress))

    with_eta = [s for s in seen if s['eta']]
    assert with_eta, f"no ETA was ever reported (saw {len(seen)} updates)"
    # And a transfer rate rides along with it
    assert any(s['speed'] for s in seen)
    # The countdown shrinks as the album progresses
    assert with_eta[0]['eta'] != with_eta[-1]['eta']


def test_finishing_clears_the_countdown(monkeypatch, tmp_path):
    """A completed download must not keep showing "2m 30s left"."""
    qm, did = _download_state(monkeypatch, tmp_path)
    qm.download_states[did] = {'id': did, 'status': 'downloading', 'progress': 50,
                               'speed': '1.20MiB/s', 'eta': '2m 30s'}

    qm.update_download_status(did, 'downloading', progress=60, speed='2MiB/s', eta='1m 00s')
    assert qm.download_states[did]['eta'] == '1m 00s'

    qm.update_download_status(did, 'completed', progress=100)
    assert qm.download_states[did]['eta'] == ''
    assert qm.download_states[did]['speed'] == ''

    db = __import__('backend.db_models', fromlist=['get_db']).get_db()
    try:
        row = db.execute("SELECT eta, speed FROM downloads WHERE id = ?", (did,)).fetchone()
        assert (row['eta'] or '') == ''
        assert (row['speed'] or '') == ''
    finally:
        db.close()


@pytest.mark.parametrize("terminal", ['error', 'cancelled', 'cleaned'])
def test_every_terminal_status_clears_it(monkeypatch, tmp_path, terminal):
    qm, did = _download_state(monkeypatch, tmp_path)
    qm.download_states[did] = {'id': did, 'status': 'downloading', 'progress': 50,
                               'speed': '1MiB/s', 'eta': '5m 00s'}
    qm.update_download_status(did, terminal)
    assert qm.download_states[did]['eta'] == ''
