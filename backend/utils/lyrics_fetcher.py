"""Fetch track lyrics from Bandcamp.

Bandcamp embeds each track's full lyrics in the track page's ``data-tralbum``
JSON (album pages only carry a ``has_lyrics`` flag, not the text). YouTube and
SoundCloud don't publish lyrics, so this is Bandcamp-only. Fetching happens in
the background after a download finishes — never on the validation path.
"""
import html
import json
import re
import urllib.request

from backend.logger import app_logger

_TRALBUM_RE = re.compile(r'data-tralbum="([^"]+)"')

# One page fetch per track — cap so a giant discography can't stall the queue.
MAX_LYRICS_TRACKS = 30


def _fetch_page(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Median)'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', 'replace')


def _track_page_lyrics(url: str) -> str:
    match = _TRALBUM_RE.search(_fetch_page(url))
    if not match:
        return ''
    data = json.loads(html.unescape(match.group(1)))
    trackinfo = data.get('trackinfo') or []
    if not trackinfo:
        return ''
    return (trackinfo[0].get('lyrics') or '').strip()


def fetch_bandcamp_lyrics(metadata: dict) -> list:
    """[{'title', 'lyrics'}] for every track that has lyrics; [] when none.

    Works for both albums (per-track page URLs from the playlist metadata)
    and single tracks (the download URL itself). Failures are per-track and
    non-fatal — lyrics are a bonus, never a blocker.
    """
    if metadata.get('is_playlist'):
        entries = [
            {'title': t.get('title', ''), 'url': t.get('url', '')}
            for t in (metadata.get('tracks') or [])[:MAX_LYRICS_TRACKS]
        ]
    else:
        entries = [{'title': metadata.get('title', ''), 'url': metadata.get('url', '')}]

    results = []
    for entry in entries:
        url = entry['url']
        if 'bandcamp.com' not in url or not url.startswith('http'):
            continue
        try:
            lyrics = _track_page_lyrics(url)
        except Exception as e:
            app_logger.debug(f"Lyrics fetch failed for {url}: {e}")
            lyrics = ''
        if lyrics:
            results.append({'title': entry['title'], 'lyrics': lyrics})

    if results:
        app_logger.info(f"Fetched lyrics for {len(results)}/{len(entries)} tracks")
    return results
