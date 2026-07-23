import subprocess
import shutil
from pathlib import Path
from typing import Optional
from backend.logger import app_logger


def get_ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def get_ffprobe_path() -> Optional[str]:
    return shutil.which("ffprobe")


def is_ffmpeg_available() -> bool:
    return get_ffmpeg_path() is not None


def run_ffmpeg(args: list, timeout: int = 3600) -> tuple:
    ffmpeg = get_ffmpeg_path() or "ffmpeg"
    cmd = [ffmpeg] + args

    app_logger.debug(f"FFmpeg: {' '.join(cmd)}")

    try:
        # encoding/errors: ffmpeg output can contain non-ASCII (titles, paths);
        # Windows' default cp1252 decode crashes the reader threads on stray bytes
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "FFmpeg timeout"
    except Exception as e:
        return -1, "", str(e)


def get_media_duration(file_path: str) -> Optional[float]:
    ffprobe = get_ffprobe_path() or "ffprobe"
    cmd = [
        ffprobe, "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


def get_media_chapters(file_path: str) -> list:
    """Embedded chapters as [{'start': float, 'end': float, 'title': str}], sorted."""
    import json
    ffprobe = get_ffprobe_path() or "ffprobe"
    cmd = [
        ffprobe, "-v", "quiet",
        "-print_format", "json",
        "-show_chapters",
        file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=30)
        if result.returncode != 0:
            return []
        chapters = []
        for ch in json.loads(result.stdout or '{}').get('chapters', []):
            chapters.append({
                'start': float(ch.get('start_time') or 0),
                'end': float(ch.get('end_time') or 0),
                'title': (ch.get('tags') or {}).get('title', ''),
            })
        chapters.sort(key=lambda c: c['start'])
        return chapters
    except Exception:
        return []


def validate_media_file(file_path: str) -> bool:
    ffprobe = get_ffprobe_path() or "ffprobe"
    cmd = [ffprobe, "-v", "quiet", "-i", file_path]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        return result.returncode == 0
    except Exception:
        return False


def get_video_dimensions(file_path: str) -> Optional[tuple]:
    """Return (width, height) of the first video stream, or None if unavailable."""
    ffprobe = get_ffprobe_path() or "ffprobe"
    cmd = [
        ffprobe, "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            w, _, h = result.stdout.strip().partition('x')
            return int(w), int(h)
    except Exception:
        pass
    return None
