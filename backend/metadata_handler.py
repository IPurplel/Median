import asyncio
import json
import re
from typing import Optional, Dict, Any
from backend.utils.cache_manager import metadata_cache
from backend.utils.validators import detect_platform, is_playlist_url
from backend.logger import app_logger

_inflight: dict[str, asyncio.Lock] = {}


async def extract_metadata(url: str, force_refresh: bool = False) -> Dict[str, Any]:
    if not force_refresh:
        cached = metadata_cache.get(url)
        if cached:
            app_logger.debug(f"Metadata cache hit: {url}")
            return cached

    lock = _inflight.setdefault(url, asyncio.Lock())
    async with lock:
        if not force_refresh:
            cached = metadata_cache.get(url)
            if cached:
                _inflight.pop(url, None)
                return cached
        result = await _do_extract(url)
        _inflight.pop(url, None)
        return result


async def _do_extract(url: str) -> Dict[str, Any]:
    try:
        import yt_dlp
        from backend.config import settings

        loop = asyncio.get_running_loop()
        platform = detect_platform(url)
        is_list   = is_playlist_url(url)

        if is_list:
            flat_opts = {
                'quiet': True, 'no_warnings': True,
                'extract_flat': 'in_playlist',
                'skip_download': True,
                'socket_timeout': 30,
            }
            def _flat():
                with yt_dlp.YoutubeDL(flat_opts) as ydl:
                    return ydl.extract_info(url, download=False)

            info_flat = await loop.run_in_executor(None, _flat)
            if not info_flat:
                return {"error": "No metadata found"}

            all_entries = list(info_flat.get('entries', []) or [])
            max_tracks = settings.MAX_PLAYLIST_TRACKS
            if len(all_entries) > max_tracks:
                app_logger.warning(
                    f"Playlist has {len(all_entries)} tracks — truncating to {max_tracks}"
                )
                all_entries = all_entries[:max_tracks]
                info_flat = dict(info_flat)
                info_flat['entries'] = all_entries

            # If flat extraction didn't return per-track durations (common for
            # Bandcamp) or titles (common for SoundCloud sets, whose flat entries
            # are bare API URLs), do a full extraction so the album's total time,
            # track titles/artists and thumbnails are accurate. Capped at
            # ENRICH_CAP tracks to keep validation fast.
            ENRICH_CAP = 60
            has_durations = any((e.get('duration') or 0) > 0 for e in all_entries if e)
            has_titles = all(
                ((e.get('title') or e.get('track') or '').strip())
                for e in all_entries if e
            )
            if (not has_durations or not has_titles) and all_entries and len(all_entries) <= ENRICH_CAP:
                enrich_opts = {
                    'quiet': True, 'no_warnings': True,
                    'extract_flat': False,
                    'skip_download': True,
                    'socket_timeout': 60,
                    'playlistend': len(all_entries),
                }
                def _enrich():
                    try:
                        with yt_dlp.YoutubeDL(enrich_opts) as ydl:
                            return ydl.extract_info(url, download=False)
                    except Exception as exc:
                        app_logger.debug(f"Duration enrichment failed: {exc}")
                        return None

                info_full = await loop.run_in_executor(None, _enrich)
                if info_full:
                    full_entries = list(info_full.get('entries', []) or [])
                    # Fields the playlist parser reads per track — copy every one
                    # the full extraction populated, not just duration.
                    _ENRICH_KEYS = (
                        'duration', 'title', 'track', 'artist', 'uploader',
                        'channel', 'thumbnail', 'thumbnails', 'webpage_url',
                    )
                    for i, fe in enumerate(full_entries):
                        if i < len(all_entries) and fe and all_entries[i]:
                            all_entries[i] = dict(all_entries[i])
                            for key in _ENRICH_KEYS:
                                val = fe.get(key)
                                if val:
                                    all_entries[i][key] = val
                    info_flat['entries'] = all_entries

            first_entry_info = None
            if all_entries:
                first_url = (all_entries[0] or {}).get('webpage_url') or \
                            (all_entries[0] or {}).get('url') or ''
                if first_url and not first_url.startswith('http'):
                    first_url = ''

                if first_url:
                    full_opts = {
                        'quiet': True, 'no_warnings': True,
                        'extract_flat': False,
                        'skip_download': True,
                        'noplaylist': True,
                        'socket_timeout': 20,
                    }
                    def _full_first(u=first_url):
                        try:
                            with yt_dlp.YoutubeDL(full_opts) as ydl:
                                return ydl.extract_info(u, download=False)
                        except Exception as exc:
                            app_logger.debug(f"First-entry full extract failed for {u!r}: {exc}")
                            return None

                    first_entry_info = await loop.run_in_executor(None, _full_first)

            metadata = _parse_metadata_playlist(info_flat, first_entry_info, url)

        else:
            full_opts = {
                'quiet': True, 'no_warnings': True,
                'extract_flat': False,
                'skip_download': True,
                'socket_timeout': 30,
            }
            def _single():
                with yt_dlp.YoutubeDL(full_opts) as ydl:
                    return ydl.extract_info(url, download=False)

            info = await loop.run_in_executor(None, _single)
            if not info:
                return {"error": "No metadata found"}
            metadata = _parse_metadata_single(info, url)

        metadata_cache.set(url, metadata)
        return metadata

    except Exception as e:
        app_logger.error(f"Metadata extraction error for {url}: {e}")
        return {"error": str(e)}


# Trailing "(Official Music Video)", "[Lyric Video]", "(Audio)", "(HD)" etc.
_TITLE_NOISE_RE = re.compile(
    r'\s*[\(\[][^\)\]]*\b(?:official|lyric[s]?|music\s+video|audio|visualizer|hd|4k|uhd)\b[^\)\]]*[\)\]]\s*$',
    re.IGNORECASE,
)


def _extract_title(info: dict) -> str:
    """Clean song title — prefer yt-dlp's parsed 'track' over the video title.

    YouTube video titles typically embed the artist as a prefix
    ('Drake - One Dance (Official Music Video)'). When yt-dlp populates
    'track' (YouTube Music / Bandcamp metadata), it is already the clean
    song name — use it. Otherwise strip a leading '{artist} - ' prefix and
    trailing noise tags so the title column in music players reads cleanly."""
    track = (info.get('track') or '').strip()
    if track:
        return track

    title = (info.get('title') or '').strip()
    if not title:
        return 'Unknown'

    artist = (
        info.get('artist') or info.get('uploader') or info.get('channel') or ''
    ).strip()
    if artist:
        for sep in (' - ', ' – ', ' — ', ' | '):
            prefix = f"{artist}{sep}"
            if title.lower().startswith(prefix.lower()):
                title = title[len(prefix):].strip()
                break

    # Strip up to two trailing noise tags (e.g. "(Official Video) (HD)").
    for _ in range(2):
        new = _TITLE_NOISE_RE.sub('', title).strip()
        if new == title:
            break
        title = new

    return title or 'Unknown'


def _extract_year(info: dict) -> str:
    """Best-effort 'YYYY' from release_date / release_year / upload_date."""
    rd = info.get('release_date') or ''
    if rd:
        digits = ''.join(c for c in str(rd) if c.isdigit())
        if len(digits) >= 4:
            return digits[:4]
    ry = info.get('release_year')
    if ry:
        return str(ry)[:4]
    ud = info.get('upload_date') or ''
    if ud and len(str(ud)) >= 4:
        return str(ud)[:4]
    return ''


def _extract_genre(info: dict) -> str:
    """Genre with platform fallbacks.

    - SoundCloud / some extractors: info['genre']
    - YouTube: no genre, use info['categories'][0] (e.g. 'Music')
    - Bandcamp: no genre, use info['tags'][0] (user-added tags;
      first is conventionally the primary genre)"""
    g = info.get('genre')
    if g:
        return g[0] if isinstance(g, list) and g else str(g)
    genres = info.get('genres')
    if isinstance(genres, list) and genres:
        return str(genres[0])
    cats = info.get('categories')
    if isinstance(cats, list) and cats:
        return str(cats[0])
    tags = info.get('tags')
    if isinstance(tags, list) and tags:
        return str(tags[0])
    return ''


def _parse_metadata_playlist(flat_info: dict, first_entry_info: dict | None, url: str) -> dict:
    entries = list(flat_info.get('entries', []) or [])
    total_duration = int(sum((e.get('duration') or 0) for e in entries if e))

    tracks = []
    for i, entry in enumerate(entries):
        if not entry:
            continue
        track_url = entry.get('webpage_url') or entry.get('url') or ''
        if not track_url:
            app_logger.debug(f"Playlist entry {i+1} has no URL — skipping")
            continue
        tracks.append({
            'index': i + 1,
            'title': _extract_title(entry) or f'Track {i+1}',
            'artist': (
                entry.get('artist') or entry.get('uploader') or
                entry.get('channel') or ''
            ),
            'duration': int(entry.get('duration') or 0),
            'url': track_url,
            'thumbnail': _best_thumbnail(entry),
        })

    if not tracks:
        app_logger.warning(f"Playlist at {url} yielded no usable tracks")

    artist = (
        flat_info.get('artist') or
        flat_info.get('album_artist') or
        flat_info.get('playlist_uploader') or
        flat_info.get('uploader') or
        flat_info.get('channel') or
        flat_info.get('creator') or ''
    )

    if not artist and first_entry_info:
        artist = (
            first_entry_info.get('artist') or
            first_entry_info.get('album_artist') or
            first_entry_info.get('uploader') or
            first_entry_info.get('channel') or
            first_entry_info.get('creator') or ''
        )

    if not artist and tracks:
        artist = tracks[0].get('artist', '')

    playlist_thumb = _best_thumbnail(flat_info)
    if not playlist_thumb and first_entry_info:
        playlist_thumb = _best_thumbnail(first_entry_info)
    if not playlist_thumb and tracks:
        playlist_thumb = tracks[0].get('thumbnail', '')

    year = _extract_year(flat_info) or (_extract_year(first_entry_info) if first_entry_info else '')
    genre = _extract_genre(flat_info) or (_extract_genre(first_entry_info) if first_entry_info else '')

    return {
        'is_playlist': True,
        'platform': detect_platform(url),
        'title': flat_info.get('title') or 'Unknown Playlist',
        'artist': artist,
        'album': flat_info.get('title') or '',
        'genre': genre,
        'year': year,
        'thumbnail': playlist_thumb,
        'track_count': len(tracks),
        'total_duration': total_duration,
        'tracks': tracks,
        'url': url,
        'formats': _get_available_formats(flat_info),
    }


def _parse_metadata_single(info: dict, url: str) -> dict:
    formats = _get_available_formats(info)
    return {
        'is_playlist': False,
        'platform': detect_platform(url),
        'title': _extract_title(info),
        'artist': (
            info.get('artist') or
            info.get('album_artist') or
            info.get('uploader') or
            info.get('channel') or
            info.get('creator') or ''
        ),
        'album': info.get('album') or '',
        'genre': _extract_genre(info),
        'year': _extract_year(info),
        'duration': int(info.get('duration') or 0),
        'thumbnail': _best_thumbnail(info),
        'url': url,
        'formats': formats,
        'available_qualities': _get_quality_options(formats),
    }


def _best_thumbnail(info: dict) -> str:
    from backend.image_processor import upgrade_thumbnail_url

    thumbs = info.get('thumbnails')
    if thumbs and isinstance(thumbs, list):
        https_thumbs = [
            t for t in thumbs
            if isinstance(t, dict) and (t.get('url') or '').startswith('https')
        ]
        if https_thumbs:
            best = max(
                https_thumbs,
                key=lambda t: (t.get('preference') or t.get('quality') or 0,
                               t.get('width') or 0)
            )
            return upgrade_thumbnail_url(best.get('url') or '')

    return upgrade_thumbnail_url(info.get('thumbnail') or '')


def _get_available_formats(info: dict) -> list:
    formats = []
    seen = set()

    for fmt in (info.get('formats') or []):
        if not fmt:
            continue
        key = (fmt.get('ext'), fmt.get('abr') or fmt.get('vbr'))
        if key in seen:
            continue
        seen.add(key)

        fmt_info = {
            'format_id': fmt.get('format_id', ''),
            'ext': fmt.get('ext', ''),
            'acodec': fmt.get('acodec', ''),
            'vcodec': fmt.get('vcodec', ''),
            'abr': fmt.get('abr'),
            'vbr': fmt.get('vbr'),
            'filesize': fmt.get('filesize') or fmt.get('filesize_approx'),
            'quality': fmt.get('quality'),
            'height': fmt.get('height'),
        }
        formats.append(fmt_info)

    return formats[:20]


def _get_quality_options(formats: list) -> dict:
    audio_bitrates = set()
    video_resolutions = set()

    for fmt in formats:
        if fmt.get('abr') and fmt.get('vcodec') == 'none':
            audio_bitrates.add(int(fmt['abr']))
        if fmt.get('height'):
            video_resolutions.add(fmt['height'])

    return {
        'audio_bitrates': sorted(audio_bitrates, reverse=True),
        'video_resolutions': sorted(video_resolutions, reverse=True),
    }
