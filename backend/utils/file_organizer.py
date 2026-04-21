import os
from pathlib import Path
from backend.utils.validators import sanitize_filename
from backend.config import settings


def get_single_track_filename(artist: str, title: str, ext: str) -> str:
    """(artist)_(title).ext"""
    artist = sanitize_filename(artist or "Unknown")
    title = sanitize_filename(title or "Unknown")
    return f"{artist}_{title}.{ext}"


def get_album_filename(artist: str, album: str, ext: str) -> str:
    """(artist)_(album_name).ext for concatenated"""
    artist = sanitize_filename(artist or "Unknown")
    album = sanitize_filename(album or "Album")
    return f"{artist}_{album}.{ext}"


def get_playlist_folder(artist: str, album: str) -> str:
    """(artist)_(album_name)/ for individual files in album"""
    artist = sanitize_filename(artist or "Unknown")
    album = sanitize_filename(album or "Album")
    return f"{artist}_{album}"


def get_track_in_album_filename(title: str, ext: str, index: int = None) -> str:
    """(title).ext or (index)_(title).ext for tracks in album folder"""
    title = sanitize_filename(title or "Unknown")
    if index is not None:
        return f"{index:02d}_{title}.{ext}"
    return f"{title}.{ext}"


def ensure_unique_path(base_path: Path) -> Path:
    """Ensure path is unique, add counter if exists."""
    if not base_path.exists():
        return base_path

    stem = base_path.stem
    suffix = base_path.suffix
    parent = base_path.parent
    counter = 1

    while True:
        new_path = parent / f"{stem}_{counter}{suffix}"
        if not new_path.exists():
            return new_path
        counter += 1


def get_download_base_path() -> Path:
    return Path(settings.UPLOAD_FOLDER)


def format_file_size(size_bytes) -> str:
    """Human readable file size. Handles None and 0 safely."""
    if not size_bytes:
        return "0 B"
    size_bytes = int(size_bytes)
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    else:
        return f"{size_bytes / 1024 ** 3:.2f} GB"


def format_duration(seconds) -> str:
    """Format duration as HH:MM:SS or MM:SS.
    Accepts int or float (yt-dlp returns floats like 213.456).
    """
    if not seconds:
        return "0:00"
    # Cast to int — yt-dlp returns floats; %02d requires int
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
