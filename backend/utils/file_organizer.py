import os
from pathlib import Path
from typing import Optional
from backend.utils.validators import sanitize_filename
from backend.config import settings

_KB = 1024
_MB = _KB * _KB
_GB = _MB * _KB

_MAX_UNIQUE_ATTEMPTS = 9_999


def get_single_track_filename(artist: str, title: str, ext: str) -> str:
    artist = sanitize_filename(artist or "Unknown")
    title = sanitize_filename(title or "Unknown")
    return f"{artist} - {title}.{ext}"


def get_album_filename(artist: str, album: str, ext: str) -> str:
    artist = sanitize_filename(artist or "Unknown")
    album = sanitize_filename(album or "Album")
    return f"{artist} - {album}.{ext}"


def get_playlist_folder(artist: str, album: str) -> str:
    artist = sanitize_filename(artist or "Unknown")
    album = sanitize_filename(album or "Album")
    return f"{artist} - {album}"


def get_track_in_album_filename(title: str, ext: str, index: int = None) -> str:
    title = sanitize_filename(title or "Unknown")
    if index is not None:
        return f"{index:02d} - {title}.{ext}"
    return f"{title}.{ext}"


def ensure_unique_path(base_path: Path) -> Path:
    if not base_path.exists():
        return base_path

    stem = base_path.stem
    suffix = base_path.suffix
    parent = base_path.parent
    counter = 1

    while counter <= _MAX_UNIQUE_ATTEMPTS:
        new_path = parent / f"{stem} ({counter}){suffix}"
        if not new_path.exists():
            return new_path
        counter += 1

    raise RuntimeError(
        f"Could not find a unique path for {base_path.name!r} "
        f"after {_MAX_UNIQUE_ATTEMPTS} attempts"
    )


def format_file_size(size_bytes) -> str:
    if not size_bytes:
        return "0 B"
    size_bytes = int(size_bytes)
    if size_bytes < _KB:
        return f"{size_bytes} B"
    elif size_bytes < _MB:
        return f"{size_bytes / _KB:.1f} KB"
    elif size_bytes < _GB:
        return f"{size_bytes / _MB:.1f} MB"
    else:
        return f"{size_bytes / _GB:.2f} GB"


def format_duration(seconds) -> str:
    if not seconds:
        return "0:00"
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def find_downloaded_file(template_base: str, ext: str) -> Optional[str]:
    EXT_ALIASES = {
        'aac': ['aac', 'm4a'],
        'm4a': ['m4a', 'aac'],
        'mp3': ['mp3'],
        'flac': ['flac'],
        'mp4': ['mp4'],
        'mkv': ['mkv'],
        'webm': ['webm'],
        'opus': ['opus', 'webm'],
        'jpg': ['jpg', 'jpeg', 'webp'],
        'png': ['png', 'webp'],
    }
    extensions_to_try = EXT_ALIASES.get(ext, [ext])

    parent = Path(template_base).parent
    stem = Path(template_base).name

    for try_ext in extensions_to_try:
        direct = Path(f"{template_base}.{try_ext}")
        if direct.exists():
            return str(direct)

    if parent.exists():
        for f in sorted(parent.iterdir()):
            if f.name.startswith(stem) and f.suffix.lower().lstrip('.') in extensions_to_try:
                return str(f)

    return None
