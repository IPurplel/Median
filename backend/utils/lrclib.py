"""Time-synced lyrics from LRCLIB (lrclib.net).

Bandcamp publishes lyric *text* but no timings, so embedded lyrics can only be
shown as a static block — ID3's USLT frame is literally the "unsynchronized"
one. LRCLIB is a free, community-contributed database of synced lyrics in LRC
format, matched on artist/title/album/duration.

When LRCLIB has a match the track gets a `.lrc` sidecar, which players such as
AIMP pick up automatically and scroll along with playback. When it doesn't —
which is common for obscure releases, and for anything while the service is
down — nothing changes and the existing static lyrics stand. This is strictly
additive: no failure here should ever affect a finished download.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.logger import app_logger

_API = "https://lrclib.net/api"
_UA = "Median/1.0 (https://github.com/IPurplel/Median)"

# LRCLIB matches on duration; a couple of seconds of drift between the source
# and whoever contributed the lyrics is normal, more than a few means it is
# probably a different cut of the song.
_DURATION_TOLERANCE = 5

# Be a polite client — a discography can mean hundreds of lookups.
_REQUEST_GAP = 0.25

AUDIO_EXTS = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.mp4', '.mkv'}


def _request(path: str, params: dict):
    """GET a JSON endpoint. Returns None on any failure — the caller treats a
    miss and an outage identically, because both mean "keep the static text"."""
    url = f"{_API}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': _UA})
    try:
        with urllib.request.urlopen(req, timeout=settings.LRCLIB_TIMEOUT) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code != 404:      # 404 just means "no lyrics for this track"
            app_logger.debug(f"LRCLIB {path} returned HTTP {e.code}")
        return None
    except Exception as e:
        app_logger.debug(f"LRCLIB {path} failed: {e}")
        return None


def _synced_from(entry) -> Optional[str]:
    if not isinstance(entry, dict) or entry.get('instrumental'):
        return None
    synced = (entry.get('syncedLyrics') or '').strip()
    return synced or None


def fetch_synced(
    artist: str,
    title: str,
    album: str = '',
    duration: int = 0,
) -> Optional[str]:
    """LRC text for a track, or None if LRCLIB has no synced match.

    Tries the exact endpoint first (artist + title + album + duration), then
    falls back to a search, since album names often differ between Bandcamp and
    whoever contributed the lyrics.
    """
    artist, title = (artist or '').strip(), (title or '').strip()
    if not artist or not title:
        return None

    params = {'artist_name': artist, 'track_name': title}
    if album:
        params['album_name'] = album
    if duration:
        params['duration'] = int(duration)

    synced = _synced_from(_request('get', params))
    if synced:
        return synced

    results = _request('search', {'artist_name': artist, 'track_name': title})
    if not isinstance(results, list):
        return None

    for entry in results:
        synced = _synced_from(entry)
        if not synced:
            continue
        # Without a duration to check against, take the first synced hit; with
        # one, require it to be the same length so we don't attach a remix's
        # timings to the album cut.
        if duration:
            found = entry.get('duration') or 0
            if abs(found - duration) > _DURATION_TOLERANCE:
                continue
        return synced

    return None


def write_lrc(audio_path, lrc_text: str) -> bool:
    """Write `<track>.lrc` beside the audio. Players find it by filename."""
    if not lrc_text or not lrc_text.strip():
        return False
    path = Path(audio_path)
    text = lrc_text if lrc_text.endswith('\n') else lrc_text + '\n'
    try:
        path.with_suffix('.lrc').write_text(text, encoding='utf-8')
        return True
    except OSError as e:
        app_logger.warning(f"Could not write .lrc beside {path.name}: {e}")
        return False


def _track_lookup(metadata: dict) -> list:
    return [t for t in (metadata.get('tracks') or []) if isinstance(t, dict)]


def attach_synced_lyrics(file_path: str, metadata: dict, merged: bool) -> int:
    """Fetch and write .lrc sidecars for a finished download.

    Returns how many tracks got synced lyrics. Blocking — call in an executor.

    Merged albums are skipped: one file holds every track, so the timings would
    have to be re-based onto each song's offset within the merge, and crossfade
    shifts those offsets. The static combined text already embedded stays.
    """
    if merged:
        return 0

    path = Path(file_path)
    artist = (metadata.get('artist') or '').strip()
    album = (metadata.get('album') or metadata.get('title') or '').strip()
    written = 0

    if path.is_file():
        synced = fetch_synced(
            artist, (metadata.get('title') or '').strip(),
            album, int(metadata.get('duration') or 0),
        )
        return 1 if (synced and write_lrc(path, synced)) else 0

    if not path.is_dir():
        return 0

    from backend.utils.tag_writer import read_title

    tracks = _track_lookup(metadata)
    by_title = {
        (t.get('title') or '').strip().lower(): t
        for t in tracks if (t.get('title') or '').strip()
    }

    audio_files = sorted(
        f for f in path.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS
    )

    for index, audio in enumerate(audio_files):
        # Prefer the file's own title tag, then the album's track list by
        # position — the same matching the static lyrics embed uses.
        title = (read_title(str(audio)) or '').strip()
        entry = by_title.get(title.lower())
        if entry is None and index < len(tracks):
            entry = tracks[index]
            title = title or (entry.get('title') or '').strip()
        if not title:
            continue

        synced = fetch_synced(
            artist, title, album,
            int((entry or {}).get('duration') or 0),
        )
        if synced and write_lrc(audio, synced):
            written += 1
        time.sleep(_REQUEST_GAP)

    if audio_files:
        # Logged even when nothing matched, so "LRCLIB has no lyrics for this
        # album" is distinguishable from "the lookup never ran".
        app_logger.info(
            f"Synced lyrics: {written}/{len(audio_files)} track(s) matched on LRCLIB"
        )
    return written
