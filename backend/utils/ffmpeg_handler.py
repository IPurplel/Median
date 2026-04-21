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
    """Run ffmpeg command, return (returncode, stdout, stderr)."""
    ffmpeg = get_ffmpeg_path() or "ffmpeg"
    cmd = [ffmpeg] + args

    app_logger.debug(f"FFmpeg: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "FFmpeg timeout"
    except Exception as e:
        return -1, "", str(e)


def get_media_duration(file_path: str) -> Optional[float]:
    """Get duration in seconds using ffprobe."""
    ffprobe = get_ffprobe_path() or "ffprobe"
    cmd = [
        ffprobe, "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


def validate_media_file(file_path: str) -> bool:
    """Validate media file is readable by ffprobe."""
    ffprobe = get_ffprobe_path() or "ffprobe"
    cmd = [ffprobe, "-v", "quiet", "-i", file_path]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        return result.returncode == 0
    except Exception:
        return False
