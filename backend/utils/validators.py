import re
import unicodedata
from urllib.parse import urlparse
from typing import Optional, Tuple


PLATFORM_PATTERNS = {
    "youtube": [
        r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=[\w-]+",
        r"(?:https?://)?(?:www\.)?youtube\.com/playlist\?list=[\w-]+",
        r"(?:https?://)?youtu\.be/[\w-]+",
        r"(?:https?://)?(?:www\.)?youtube\.com/channel/[\w-]+",
        r"(?:https?://)?(?:www\.)?youtube\.com/@[\w-]+",
    ],
    "soundcloud": [
        r"(?:https?://)?(?:www\.)?soundcloud\.com/[\w-]+/[\w-]+",
        r"(?:https?://)?(?:www\.)?soundcloud\.com/[\w-]+/sets/[\w-]+",
        r"(?:https?://)?(?:www\.)?soundcloud\.com/[\w-]+",
    ],
    "bandcamp": [
        r"(?:https?://)?[\w-]+\.bandcamp\.com/track/[\w-]+",
        r"(?:https?://)?[\w-]+\.bandcamp\.com/album/[\w-]+",
        r"(?:https?://)?[\w-]+\.bandcamp\.com",
    ],
}

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)


def is_valid_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


def detect_platform(url: str) -> Optional[str]:
    if not url:
        return None

    url = url.strip()

    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if re.match(pattern, url, re.IGNORECASE):
                return platform

    return None


def is_playlist_url(url: str) -> bool:
    playlist_patterns = [
        r"youtube\.com/playlist\?list=",
        r"soundcloud\.com/[\w-]+/sets/",
        r"bandcamp\.com/album/",
        r"youtube\.com/@[\w-]+",
        r"youtube\.com/channel/",
        r"soundcloud\.com/[\w-]+$",
    ]
    for pattern in playlist_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


def sanitize_filename(name: str) -> str:
    # Normalize Unicode so composed/decomposed forms are consistent
    name = unicodedata.normalize('NFC', name)
    invalid = r'[<>:"/\\|?*\x00-\x1f]'
    sanitized = re.sub(invalid, '', name)
    return sanitized[:200].strip('. ') or 'Unknown'


ORIGINAL_BITRATE = 'original'


def validate_bitrate(bitrate: str) -> str:
    """Return the bitrate string if valid, raise ValueError otherwise.

    'original' is a valid choice meaning "don't re-encode at all" — the source
    stream is kept exactly as served.
    """
    if not bitrate:
        return bitrate
    value = bitrate.strip()
    if value.lower() == ORIGINAL_BITRATE:
        return ORIGINAL_BITRATE
    if not re.fullmatch(r'\d{1,6}k?', value, re.IGNORECASE):
        raise ValueError(f"Invalid bitrate value: {bitrate!r}")
    return value


def validate_url(url: str, max_length: int = 2048) -> Tuple[bool, Optional[str], Optional[str]]:
    if not url or not url.strip():
        return False, None, "URL cannot be empty"

    url = url.strip()

    if len(url) > max_length:
        return False, None, f"URL is too long (max {max_length} characters)"

    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = "https://" + url
            parsed = urlparse(url)

        if not parsed.netloc:
            return False, None, "Invalid URL format"
    except Exception:
        return False, None, "Invalid URL format"

    platform = detect_platform(url)
    if not platform:
        return False, None, "Platform not supported. Supported: YouTube, SoundCloud, Bandcamp"

    return True, platform, None
