"""Find the YouTube upload that corresponds to a known recording.

Spotify tells Median what a song *is* (title, artist, exact duration) but not
where to get it. This module bridges that gap: it searches YouTube and scores
the candidates, because "first search result" is wrong often enough to matter.

Duration is the strongest signal available and is weighted accordingly. A
worked example from the real search results for Daft Punk's "Get Lucky": the
top hit is the official audio at 4:09, the *album* version Spotify lists is
6:09, and a live Grammy medley also charts. Only the length tells them apart —
ranking by apparent officialness would quietly hand back the radio edit.

Without an ISRC (Spotify's embed player doesn't expose one) every match is a
best guess. Each result therefore carries a confidence, and anything below
CONFIDENT is reported to the user rather than silently accepted.
"""
import asyncio
import re
from typing import Optional

from backend.config import settings
from backend.logger import app_logger

# Below this, the caller warns the user that the match may be the wrong version.
CONFIDENT = 0.55

# Past this many seconds apart, two recordings are not the same take — a radio
# edit, an extended mix or a live rendition, but not what was asked for.
MAX_DURATION_DELTA = 20

# Words that mark a *different* rendition. Only penalised when the wanted title
# doesn't contain them too: someone asking for "Live at Wembley" or a song
# genuinely called "Remix" should not be fighting their own search terms.
_VARIANT_WORDS = (
    'live', 'cover', 'remix', 'karaoke', 'instrumental', 'acoustic',
    'nightcore', 'sped up', 'slowed', 'reverb', '8d', 'bass boosted',
    'reaction', 'tutorial', 'mashup', 'medley', 'concert', 'session',
)

# Noise in YouTube titles that carries no identifying information.
_NOISE_WORDS = {
    'official', 'video', 'audio', 'lyrics', 'lyric', 'music', 'hd', 'hq',
    'k', '4k', '1080p', 'mv', 'full', 'the', 'a', 'an', 'ft', 'feat',
    'featuring', 'with', 'and', 'explicit', 'remastered', 'visualizer',
}

_WORD_RE = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> set:
    """Comparable word set: lowercased, punctuation-free, noise removed."""
    words = _WORD_RE.findall((text or '').lower())
    return {w for w in words if w not in _NOISE_WORDS}


def _coverage(wanted: set, found: set) -> float:
    """How much of the wanted title the candidate accounts for (0..1)."""
    if not wanted:
        return 0.0
    return len(wanted & found) / len(wanted)


def _is_topic_channel(channel: str) -> bool:
    """YouTube's auto-generated 'Artist - Topic' channels.

    These are uploaded by the label's distributor, not a fan: they carry the
    clean master with no intro, outro or video soundtrack over the top. When
    one exists it is nearly always the right answer.
    """
    return (channel or '').strip().lower().endswith('- topic')


def score_candidate(candidate: dict, title: str, artist: str,
                    duration: int = 0) -> float:
    """0..1 confidence that `candidate` is the recording described.

    Returns 0 for candidates that are disqualified outright.
    """
    cand_title = candidate.get('title') or ''
    channel = candidate.get('channel') or candidate.get('uploader') or ''
    cand_duration = int(candidate.get('duration') or 0)

    if not cand_title or not (candidate.get('url') or candidate.get('id')):
        return 0.0

    # A stream that never ends is never a song.
    if (candidate.get('live_status') or '') in ('is_live', 'is_upcoming'):
        return 0.0

    wanted_title = _tokens(title)
    wanted_artist = _tokens(artist)
    found = _tokens(cand_title)
    found_channel = _tokens(channel)

    # ── duration ──
    if duration and cand_duration:
        delta = abs(cand_duration - duration)
        if delta > MAX_DURATION_DELTA:
            return 0.0
        duration_score = 1.0 - (delta / MAX_DURATION_DELTA)
        duration_weight = 0.45
    else:
        # Unknown either side: don't reward or punish, just redistribute the
        # weight onto the signals that do exist.
        duration_score = 0.0
        duration_weight = 0.0

    title_score = _coverage(wanted_title, found)
    # The artist may be in the title, the channel, or both.
    artist_score = max(
        _coverage(wanted_artist, found),
        _coverage(wanted_artist, found_channel),
    )

    title_weight, artist_weight = 0.35, 0.20
    total_weight = duration_weight + title_weight + artist_weight
    score = (
        duration_score * duration_weight
        + title_score * title_weight
        + artist_score * artist_weight
    ) / total_weight

    # ── adjustments ──
    if _is_topic_channel(channel):
        score += 0.15

    lower_cand = cand_title.lower()
    lower_wanted = f"{title} {artist}".lower()
    # Each additional marker is more evidence this is a different rendition:
    # "Live Grammy Performance (Medley)" is further from the studio track than
    # something merely tagged "Live". Capped so two flags is as bad as five.
    flags = sum(
        1 for word in _VARIANT_WORDS
        if word in lower_cand and word not in lower_wanted
    )
    score -= 0.30 * min(flags, 2)

    return max(0.0, min(1.0, score))


def _search_query(title: str, artist: str) -> str:
    return ' - '.join(p for p in ((artist or '').strip(), (title or '').strip()) if p)


def _search(query: str, limit: int) -> list:
    """Flat YouTube search — metadata only, nothing downloaded."""
    import yt_dlp

    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'skip_download': True,
        'socket_timeout': 30,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f'ytsearch{limit}:{query}', download=False)
    except Exception as e:
        app_logger.warning(f"YouTube search failed for {query!r}: {e}")
        return []
    return [e for e in (info or {}).get('entries') or [] if e]


class Match:
    """A chosen YouTube upload, with why it should or shouldn't be trusted.

    `alternatives` are the runner-up URLs from the same search, best first.
    They cost nothing extra to collect and give the downloader somewhere to go
    when the chosen upload turns out to be undownloadable in practice.
    """

    __slots__ = ('url', 'confidence', 'title', 'channel', 'duration',
                 'alternatives')

    def __init__(self, url: str, confidence: float, title: str = '',
                 channel: str = '', duration: int = 0, alternatives=None):
        self.url = url
        self.confidence = confidence
        self.title = title
        self.channel = channel
        self.duration = duration
        self.alternatives = list(alternatives or [])

    @property
    def is_confident(self) -> bool:
        return self.confidence >= CONFIDENT

    def __repr__(self):
        return f'<Match {self.confidence:.2f} {self.title!r}>'


def _candidate_url(candidate: dict) -> str:
    return candidate.get('url') or f"https://www.youtube.com/watch?v={candidate.get('id')}"


def _is_downloadable(url: str) -> bool:
    """Can this actually be fetched, or does it only *appear* in search?

    A flat search lists videos that are region-locked, removed, or otherwise
    unplayable — YouTube keeps them in the index. Taking the top-scoring result
    on faith fails often enough to look broken (two of three tracks on the
    first real album tested), so the chosen candidate is confirmed before it is
    handed to the downloader.
    """
    import yt_dlp

    opts = {
        'quiet': True, 'no_warnings': True, 'skip_download': True,
        'socket_timeout': 20, 'noplaylist': True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return bool(info)
    except Exception as e:
        app_logger.debug(f"Candidate unavailable {url}: {e}")
        return False


def match_track_sync(title: str, artist: str, duration: int = 0) -> Optional[Match]:
    """Best *downloadable* YouTube upload for one recording, else None.

    Blocking; call through match_track() from async code.
    """
    query = _search_query(title, artist)
    if not query:
        return None

    candidates = _search(query, settings.YT_MATCH_RESULTS)

    def ranked(want_duration: int, penalty: float) -> list:
        scored = [
            (score_candidate(c, title, artist, want_duration) * penalty, c)
            for c in candidates
        ]
        scored = [pair for pair in scored if pair[0] > 0]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored

    # Preferred pool: the right length. Fallback pool: ignore length entirely,
    # at half confidence. The fallback is needed both when nothing matches the
    # duration *and* when the only thing that did turns out to be
    # undownloadable — otherwise one dead video loses the track outright.
    pools = [ranked(duration, 1.0)]
    if duration:
        pools.append(ranked(0, 0.5))

    # Everything plausible, best first, deduplicated across both pools. The
    # chosen one is verified; the rest ride along as fallbacks in case it turns
    # out to be undownloadable when the time comes.
    ordered, seen = [], set()
    for pool in pools:
        for score, candidate in pool:
            url = _candidate_url(candidate)
            if url in seen:
                continue
            seen.add(url)
            ordered.append((score, candidate, url))

    for i, (score, candidate, url) in enumerate(ordered[:settings.YT_MATCH_VERIFY]):
        if not _is_downloadable(url):
            continue
        return Match(
            url=url,
            confidence=score,
            title=candidate.get('title') or '',
            channel=candidate.get('channel') or candidate.get('uploader') or '',
            duration=int(candidate.get('duration') or 0),
            alternatives=[u for _, _, u in ordered[i + 1:]][:settings.YT_MATCH_FALLBACKS],
        )
    return None


async def match_track(title: str, artist: str, duration: int = 0) -> Optional[Match]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, match_track_sync, title, artist, duration
    )


async def match_all(tracks: list, on_progress=None) -> list:
    """Match every track, a few at a time. Results align 1:1 with `tracks`.

    Bounded concurrency: searches are quick but each spawns a thread, and an
    unbounded gather over a 100-track playlist would swamp the executor the
    downloads themselves need.
    """
    semaphore = asyncio.Semaphore(max(1, settings.SPOTIFY_MATCH_CONCURRENCY))
    done = [0]
    total = len(tracks)

    async def _one(track):
        async with semaphore:
            try:
                match = await match_track(
                    track.get('title', ''), track.get('artist', ''),
                    int(track.get('duration') or 0),
                )
            except Exception as e:
                app_logger.warning(
                    f"Match failed for {track.get('title')!r}: {e}"
                )
                match = None
            done[0] += 1
            if on_progress:
                await on_progress(done[0], total, track, match)
            return match

    return await asyncio.gather(*(_one(t) for t in tracks))
