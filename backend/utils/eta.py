"""Estimated time remaining for a download.

Two different estimates are needed, because they answer different questions.

yt-dlp reports an ETA for the *file it is currently fetching*, computed from the
live transfer rate. For a single track that is exactly what the user wants. For
a twelve-track album it is actively misleading — it would read "3 seconds" while
eleven tracks are still queued.

So multi-track jobs get a job-level estimate instead: how long the work done so
far took, extrapolated across what is left. That is cruder than a byte-rate
figure, which is why the estimate is withheld until there is enough evidence to
be worth showing, and smoothed so it counts down steadily rather than lurching
about whenever one track happens to be slower than the last.
"""
import time
from typing import Optional


def format_eta(seconds: Optional[float]) -> str:
    """Human-readable remaining time: '45s', '2m 30s', '1h 05m'.

    Returns '' for anything unknown or nonsensical, which callers treat as
    "say nothing" — a wrong estimate is worse than no estimate.
    """
    if seconds is None:
        return ''
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ''
    if total < 0:
        return ''
    if total < 60:
        # Sub-5s countdowns just flicker; "a few seconds" is honest and calm.
        return f"{total}s" if total >= 5 else 'a few seconds'
    if total < 3600:
        minutes, secs = divmod(total, 60)
        return f"{minutes}m {secs:02d}s"
    hours, rest = divmod(total, 3600)
    return f"{hours}h {rest // 60:02d}m"


def normalize_ytdlp_eta(raw) -> str:
    """yt-dlp's own ETA (seconds, or its pre-formatted 'MM:SS') → our format.

    yt-dlp hands over `eta` as a number and `_eta_str` as text like '00:42' or
    'Unknown'. Both are accepted so callers can pass whichever they have.
    """
    if raw is None:
        return ''
    if isinstance(raw, (int, float)):
        return format_eta(raw)

    text = str(raw).strip()
    if not text or text.lower() in ('unknown', 'n/a', '--:--'):
        return ''
    parts = text.split(':')
    try:
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + int(part)
    except ValueError:
        return ''
    return format_eta(seconds)


class JobEta:
    """Whole-job time remaining, extrapolated from progress so far.

    Feed it a completed fraction (0..1) whenever progress moves; it returns
    seconds remaining, or None while an estimate would be guesswork.
    """

    def __init__(self, smoothing: float = 0.35, min_elapsed: float = 5.0,
                 min_fraction: float = 0.10):
        # How much each new reading moves the estimate. Low values are steady
        # but slow to react when the transfer rate genuinely changes.
        self.smoothing = smoothing
        # Below these, the sample is too small to extrapolate from. Measured on
        # a real 13-track album: at 8% done the figure swung by a full minute
        # between updates, because one slow track is most of the evidence that
        # far in. Waiting for a tenth of the job costs a few seconds of "no
        # estimate yet" and buys a number that doesn't embarrass itself.
        self.min_elapsed = min_elapsed
        self.min_fraction = min_fraction
        self._start = time.monotonic()
        self._estimate: Optional[float] = None
        self._last_fraction = 0.0
        self._last_update = self._start

    def reset(self) -> None:
        """Start over — used when a failed attempt rewinds the progress bar."""
        self._start = time.monotonic()
        self._estimate = None
        self._last_fraction = 0.0
        self._last_update = self._start

    def update(self, fraction: float) -> Optional[float]:
        """Record progress, return seconds remaining (or None if not yet sure)."""
        try:
            fraction = float(fraction)
        except (TypeError, ValueError):
            return self._estimate

        now = time.monotonic()

        # Progress went backwards: a retry restarted the work, so timings from
        # the abandoned attempt no longer describe what is left to do.
        if fraction < self._last_fraction - 0.01:
            self.reset()
            self._last_fraction = max(0.0, fraction)
            return None

        self._last_fraction = fraction
        self._last_update = now

        if fraction >= 1.0:
            self._estimate = 0.0
            return 0.0

        elapsed = now - self._start
        if fraction < self.min_fraction or elapsed < self.min_elapsed:
            return None

        raw = elapsed * (1.0 - fraction) / fraction
        if self._estimate is None:
            self._estimate = raw
        else:
            # Asymmetric on purpose. Tracks vary in size, so the raw figure
            # oscillates by several seconds either way; a countdown seen going
            # *up* reads as something being wrong, while one that falls just
            # reads as good news. Rises are therefore damped harder — enough to
            # absorb that jitter, but not so much that a genuine slowdown is
            # hidden, since the estimate still climbs when the trend is real.
            delta = raw - self._estimate
            weight = self.smoothing if delta < 0 else self.smoothing * 0.4
            self._estimate += weight * delta
        return self._estimate

    def remaining_text(self, fraction: float) -> str:
        """update() plus formatting — '' while the estimate is still unreliable."""
        return format_eta(self.update(fraction))


class TrackWeights:
    """Turns "track 4 of 16, 30% through it" into a fraction of the whole job.

    Counting tracks equally makes the estimate lurch, because tracks are not
    equal: an album with a 28-second interlude next to a 7-minute closer will
    race through 1/16th of its "progress" and then appear to stall. Weighting
    by track length fixes that, and the durations are already known — every
    supported source reports them before the download starts.

    Falls back to equal weights when durations are missing, which is no worse
    than counting tracks and never divides by zero.
    """

    def __init__(self, tracks: list):
        durations = [max(0.0, float((t or {}).get('duration') or 0)) for t in tracks]
        # All-zero (or empty) means we know nothing about relative sizes.
        if not durations or sum(durations) <= 0:
            durations = [1.0] * max(len(durations), 1)
        self.weights = durations
        self.total = sum(durations)

        self.cumulative = []
        running = 0.0
        for weight in durations:
            self.cumulative.append(running)
            running += weight

    def fraction(self, index: int, within: float = 0.0) -> float:
        """Job fraction complete, given the track being worked on and how far
        into it we are (0..1)."""
        if index >= len(self.weights):
            return 1.0
        index = max(0, index)
        within = min(max(within, 0.0), 1.0)
        done = self.cumulative[index] + self.weights[index] * within
        return min(1.0, done / self.total) if self.total else 0.0
