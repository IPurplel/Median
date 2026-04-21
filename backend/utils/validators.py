import re
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


def detect_platform(url: str) -> Optional[str]:
    """Detect platform from URL."""
    if not url:
        return None

    url = url.strip()

    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if re.match(pattern, url, re.IGNORECASE):
                return platform

    return None


def is_playlist_url(url: str) -> bool:
    """Check if URL is a playlist/album."""
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
    """Remove invalid filename characters."""
    invalid = r'[<>:"/\\|?*\x00-\x1f]'
    sanitized = re.sub(invalid, '', name)
    sanitized = sanitized.replace(' ', '_')
    sanitized = re.sub(r'_+', '_', sanitized)
    return sanitized[:200].strip('._')


def validate_url(url: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validate URL.
    Returns (is_valid, platform, error_message)
    """
    if not url or not url.strip():
        return False, None, "URL cannot be empty"

    url = url.strip()

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
