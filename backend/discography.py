"""Resolve an artist's full discography from a single album URL.

Given any album/playlist URL Median already accepts, work out where that
platform lists the artist's other releases, then return every album found
there. Each album is queued as its own download, so the library ends up with
one folder per album instead of everything flattened together.

Only the album URLs and titles are collected here — resolving every album's
full tracklist up front would take minutes for a large discography. That work
happens per album inside the download queue.
"""
import asyncio
import re
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from backend.config import settings
from backend.logger import app_logger
from backend.utils.cache_manager import metadata_cache
from backend.utils.validators import detect_platform

# Cache key prefix — the discography listing is stored in the same table as
# track metadata, so it needs a namespace that can never collide with a URL.
_CACHE_PREFIX = 'discography::'
_CACHE_TTL = 6 * 3600

_BANDCAMP_HOST_RE = re.compile(r'^([\w-]+\.bandcamp\.com)$', re.IGNORECASE)
_SOUNDCLOUD_USER_RE = re.compile(r'^/([\w-]+)', re.IGNORECASE)


def _clean_url(url: str) -> str:
    """Normalize an album URL so the same album can't appear twice.

    Tracking junk (Bandcamp's ?from=hp, #fragments) is dropped, but a YouTube
    album is identified *only* by its ?list= id — strip that and every album on
    a channel collapses into one unusable https://youtube.com/playlist.
    """
    p = urlparse(url)
    list_id = parse_qs(p.query).get('list', [''])[0]
    query = urlencode({'list': list_id}) if list_id else ''
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip('/'), '', query, ''))


def _title_from_url(url: str) -> str:
    """Human-readable title from an album slug, for platforms whose listing
    pages report links without titles (Bandcamp's /music grid). The real title
    replaces this once the album's own metadata is extracted at download time."""
    slug = urlparse(url).path.rstrip('/').rsplit('/', 1)[-1]
    words = [w for w in re.split(r'[-_]+', slug) if w]
    return ' '.join(w.capitalize() for w in words) or 'Untitled Album'


def artist_pages(url: str, metadata: Optional[dict] = None) -> list:
    """Candidate 'all releases by this artist' pages, best guess first.

    Each platform buries the discography somewhere different, and not every
    artist uses every tab — so return every plausible page and let the caller
    try them in order until one yields albums.
    """
    metadata = metadata or {}
    platform = detect_platform(url) or ''
    parsed = urlparse(url if '://' in url else f'https://{url}')

    if platform == 'bandcamp':
        if _BANDCAMP_HOST_RE.match(parsed.netloc):
            return [f'https://{parsed.netloc.lower()}/music']
        return []

    if platform == 'soundcloud':
        m = _SOUNDCLOUD_USER_RE.match(parsed.path)
        if not m:
            return []
        user = m.group(1)
        # /sets holds both albums and user-made playlists; /albums is the
        # narrower "releases" view but is empty for most accounts.
        return [
            f'https://soundcloud.com/{user}/albums',
            f'https://soundcloud.com/{user}/sets',
        ]

    if platform == 'youtube':
        # A YouTube album is a playlist, and playlist URLs carry no artist —
        # the channel comes from the extracted metadata's uploader/channel URL.
        base = (metadata.get('artist_url') or '').rstrip('/')
        if not base.startswith('http'):
            return []
        return [f'{base}/releases', f'{base}/playlists']

    return []


def _is_album_entry(entry: dict, platform: str) -> bool:
    """Keep album/playlist entries, drop loose singles and non-music links."""
    if not entry:
        return False
    entry_url = entry.get('url') or entry.get('webpage_url') or ''
    if not entry_url.startswith('http'):
        return False

    if platform == 'bandcamp':
        # A Bandcamp /music grid mixes albums and standalone tracks.
        return '/album/' in entry_url
    if platform == 'youtube':
        return 'list=' in entry_url
    if platform == 'soundcloud':
        return '/sets/' in entry_url
    return True


def _entries_to_albums(entries: list, platform: str) -> list:
    albums = []
    seen = set()
    for entry in entries or []:
        if not _is_album_entry(entry, platform):
            continue
        raw_url = entry.get('url') or entry.get('webpage_url')
        album_url = _clean_url(raw_url)
        if album_url in seen:
            continue
        seen.add(album_url)

        title = (entry.get('title') or '').strip()
        albums.append({
            'url': album_url,
            'title': title or _title_from_url(album_url),
            'title_is_guess': not title,
            'thumbnail': entry.get('thumbnail') or '',
            'track_count': int(entry.get('playlist_count') or 0),
        })
        if len(albums) >= settings.MAX_DISCOGRAPHY_ALBUMS:
            app_logger.warning(
                f"Discography truncated to {settings.MAX_DISCOGRAPHY_ALBUMS} albums"
            )
            break
    return albums


async def _flat_entries(page_url: str) -> list:
    """Flat-extract a listing page. Returns [] instead of raising — a 404 on
    one candidate page just means we should try the next one."""
    import yt_dlp

    opts = {
        'quiet': True, 'no_warnings': True,
        'extract_flat': 'in_playlist',
        'skip_download': True,
        'socket_timeout': 30,
        'playlistend': settings.MAX_DISCOGRAPHY_ALBUMS,
    }

    def _extract():
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(page_url, download=False)
        except Exception as exc:
            app_logger.debug(f"Discography page {page_url!r} failed: {exc}")
            return None

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _extract)
    if not info:
        return []
    return list(info.get('entries') or [])


# Bandcamp keeps album titles in two places on a /music page: the first screen
# of releases is server-rendered into the grid, and the rest sit in a JSON blob
# the page uses to lazy-load them. yt-dlp's flat listing reports neither — only
# the links — so without this the picker would show URL slugs.
_BC_CLIENT_ITEMS_RE = re.compile(r'data-client-items="([^"]*)"')
_BC_GRID_LI_RE = re.compile(r'<li[^>]*data-item-id="[^"]*".*?</li>', re.S)
_BC_HREF_RE = re.compile(r'href="([^"]+)"')
_BC_TITLE_RE = re.compile(r'<p class="title"[^>]*>(.*?)</p>', re.S)
_BC_ARTIST_SPAN_RE = re.compile(r'<span class="artist-override".*?</span>', re.S)
_TAG_RE = re.compile(r'<[^>]+>')


def _album_path(url: str) -> str:
    return urlparse(url).path.rstrip('/').lower()


def _bandcamp_titles(page_html: str) -> dict:
    """{album path: real title} scraped from a Bandcamp /music page."""
    import html as html_mod
    import json

    titles = {}

    for li in _BC_GRID_LI_RE.findall(page_html):
        href = _BC_HREF_RE.search(li)
        raw_title = _BC_TITLE_RE.search(li)
        if not href or not raw_title:
            continue
        # The title block may carry a second line naming featured artists —
        # drop it so the picker shows the album name alone.
        text = _BC_ARTIST_SPAN_RE.sub('', raw_title.group(1))
        text = html_mod.unescape(_TAG_RE.sub(' ', text))
        title = ' '.join(text.split())
        if title:
            titles[_album_path(href.group(1))] = title

    blob = _BC_CLIENT_ITEMS_RE.search(page_html)
    if blob:
        try:
            for item in json.loads(html_mod.unescape(blob.group(1))):
                path, title = item.get('page_url'), (item.get('title') or '').strip()
                if path and title:
                    titles.setdefault(_album_path(path), title)
        except (ValueError, TypeError, AttributeError) as e:
            app_logger.debug(f"Bandcamp client-items parse failed: {e}")

    return titles


async def _enrich_bandcamp_titles(page_url: str, albums: list) -> None:
    """Replace guessed slug titles with the real ones. Best-effort: on any
    failure the albums keep their slug titles and stay downloadable."""
    import urllib.request

    if not any(a['title_is_guess'] for a in albums):
        return

    def _fetch():
        try:
            req = urllib.request.Request(
                page_url, headers={'User-Agent': 'Mozilla/5.0 (Median)'}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode('utf-8', 'replace')
        except Exception as exc:
            app_logger.debug(f"Bandcamp title fetch failed for {page_url}: {exc}")
            return ''

    loop = asyncio.get_running_loop()
    page_html = await loop.run_in_executor(None, _fetch)
    if not page_html:
        return

    # Scraping is inherently brittle — a layout change must never take down
    # the whole lookup, only the nicer titles.
    try:
        titles = _bandcamp_titles(page_html)
    except Exception as e:
        app_logger.warning(f"Bandcamp title scrape failed (non-fatal): {e}")
        return

    for album in albums:
        if not album['title_is_guess']:
            continue
        real = titles.get(_album_path(album['url']))
        if real:
            album['title'] = real
            album['title_is_guess'] = False


async def resolve_discography(
    url: str,
    metadata: Optional[dict] = None,
    force_refresh: bool = False,
) -> dict:
    """Every album by the artist behind `url`.

    Returns {'platform', 'artist', 'source_page', 'albums': [...]}. An empty
    album list is a normal outcome (artist with one release, a platform whose
    listing page is private) — not an error.
    """
    metadata = metadata or {}
    platform = detect_platform(url) or ''
    artist = (metadata.get('artist') or '').strip()

    pages = artist_pages(url, metadata)
    if not pages:
        return {
            'platform': platform,
            'artist': artist,
            'source_page': '',
            'albums': [],
            'note': "Median can't find an artist page for this URL.",
        }

    cache_key = f'{_CACHE_PREFIX}{pages[0]}'
    if not force_refresh:
        cached = metadata_cache.get(cache_key)
        if cached:
            app_logger.debug(f"Discography cache hit: {pages[0]}")
            return cached

    result = {
        'platform': platform,
        'artist': artist,
        'source_page': '',
        'albums': [],
    }

    for page in pages:
        entries = await _flat_entries(page)
        albums = _entries_to_albums(entries, platform)
        if albums:
            if platform == 'bandcamp':
                await _enrich_bandcamp_titles(page, albums)
            result['source_page'] = page
            result['albums'] = albums
            break

    if not result['albums']:
        result['note'] = "No other albums found on this artist's page."

    metadata_cache.set(cache_key, result, ttl=_CACHE_TTL)
    return result
