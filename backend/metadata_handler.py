import asyncio
import json
from typing import Optional, Dict, Any
from backend.utils.cache_manager import metadata_cache
from backend.utils.validators import detect_platform, is_playlist_url
from backend.logger import app_logger


async def extract_metadata(url: str, force_refresh: bool = False) -> Dict[str, Any]:
    """Extract metadata using yt-dlp."""
    if not force_refresh:
        cached = metadata_cache.get(url)
        if cached:
            app_logger.debug(f"Metadata cache hit: {url}")
            return cached

    try:
        import yt_dlp

        loop = asyncio.get_running_loop()
        platform = detect_platform(url)
        is_list   = is_playlist_url(url)

        if is_list:
            # ── TWO-PASS STRATEGY FOR PLAYLISTS ───────────────────────────
            # Pass 1: fast flat extract → track titles + URLs (no per-track API calls)
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

            # Pass 2: full extract of FIRST entry → artist + thumbnail
            # This one API call gives us everything the stub is missing.
            entries = list(info_flat.get('entries', []) or [])
            first_entry_info = None
            if entries:
                first_url = (entries[0] or {}).get('webpage_url') or \
                            (entries[0] or {}).get('url') or ''
                if first_url and not first_url.startswith('http'):
                    # Some platforms give bare IDs in flat entries — reconstruct URL
                    first_url = ''

                if first_url:
                    full_opts = {
                        'quiet': True, 'no_warnings': True,
                        'extract_flat': False,
                        'skip_download': True,
                        'noplaylist': True,   # only this single item, not the whole list
                        'socket_timeout': 20,
                    }
                    def _full_first(u=first_url):
                        try:
                            with yt_dlp.YoutubeDL(full_opts) as ydl:
                                return ydl.extract_info(u, download=False)
                        except Exception:
                            return None

                    first_entry_info = await loop.run_in_executor(None, _full_first)

            # Merge: use flat info for playlist-level data + first entry for artist/thumb
            metadata = _parse_metadata_playlist(info_flat, first_entry_info, url)

        else:
            # Single track — full extract (fast, single item)
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


def _parse_metadata_playlist(flat_info: dict, first_entry_info: dict | None, url: str) -> dict:
    """
    Build playlist metadata from:
      flat_info       — fast flat extract (has track list)
      first_entry_info — full extract of first track (has artist, thumbnail)
    """
    entries = list(flat_info.get('entries', []) or [])
    total_duration = int(sum((e.get('duration') or 0) for e in entries if e))

    tracks = []
    for i, entry in enumerate(entries):
        if entry:
            tracks.append({
                'index': i + 1,
                'title': entry.get('title') or f'Track {i+1}',
                'artist': (
                    entry.get('artist') or entry.get('uploader') or
                    entry.get('channel') or ''
                ),
                'duration': int(entry.get('duration') or 0),
                'url': entry.get('webpage_url') or entry.get('url') or '',
                'thumbnail': _best_thumbnail(entry),
            })

    # ── Artist: flat_info first, then enrich from first_entry_info ───────────
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

    # ── Thumbnail: prefer flat_info playlist cover, fall back to first-entry ─
    playlist_thumb = _best_thumbnail(flat_info)
    if not playlist_thumb and first_entry_info:
        playlist_thumb = _best_thumbnail(first_entry_info)
    if not playlist_thumb and tracks:
        playlist_thumb = tracks[0].get('thumbnail', '')

    return {
        'is_playlist': True,
        'platform': detect_platform(url),
        'title': flat_info.get('title') or 'Unknown Playlist',
        'artist': artist,
        'album': flat_info.get('title') or '',
        'thumbnail': playlist_thumb,
        'track_count': len(entries),
        'total_duration': total_duration,
        'tracks': tracks,
        'url': url,
        'formats': _get_available_formats(flat_info),
    }


def _parse_metadata_single(info: dict, url: str) -> dict:
    """Build single-track metadata from a full yt-dlp extract."""
    formats = _get_available_formats(info)
    return {
        'is_playlist': False,
        'platform': detect_platform(url),
        'title': info.get('title') or 'Unknown',
        'artist': (
            info.get('artist') or
            info.get('album_artist') or
            info.get('uploader') or
            info.get('channel') or
            info.get('creator') or ''
        ),
        'album': info.get('album') or '',
        'duration': int(info.get('duration') or 0),
        'thumbnail': _best_thumbnail(info),
        'url': url,
        'formats': formats,
        'available_qualities': _get_quality_options(formats),
    }


def _parse_metadata(info: dict, url: str) -> dict:
    """Legacy wrapper — routes to the appropriate parser."""
    is_playlist = info.get('_type') in ('playlist', 'multi_video') or 'entries' in info
    if is_playlist:
        return _parse_metadata_playlist(info, None, url)
    return _parse_metadata_single(info, url)


def _best_thumbnail(info: dict) -> str:
    """
    Pick the best thumbnail URL from yt-dlp info dict.
    Prefers the highest-resolution HTTPS thumbnail.
    Falls back to the top-level 'thumbnail' key.
    """
    # Try the thumbnails list first (sorted by preference/resolution)
    thumbs = info.get('thumbnails')
    if thumbs and isinstance(thumbs, list):
        # Filter to HTTPS URLs only; take the last (usually highest res)
        https_thumbs = [
            t for t in thumbs
            if isinstance(t, dict) and (t.get('url') or '').startswith('https')
        ]
        if https_thumbs:
            # Pick highest preference or last in list
            best = max(
                https_thumbs,
                key=lambda t: (t.get('preference') or t.get('quality') or 0,
                               t.get('width') or 0)
            )
            return best.get('url') or ''

    # Fall back to top-level thumbnail key
    return info.get('thumbnail') or ''


def _get_available_formats(info: dict) -> list:
    """Extract available formats."""
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

    return formats[:20]  # Cap for cache efficiency


def _get_quality_options(formats: list) -> dict:
    """Parse available quality options."""
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
