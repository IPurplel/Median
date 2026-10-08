import subprocess
import shutil
import threading
import time
from pathlib import Path
from typing import Dict, Optional
from backend.logger import app_logger


def get_ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def get_ffprobe_path() -> Optional[str]:
    return shutil.which("ffprobe")


def is_ffmpeg_available() -> bool:
    return get_ffmpeg_path() is not None


# ffmpeg runs in executor threads that asyncio cancellation can't reach, so a
# cancelled merge used to keep encoding for up to half an hour and leave its
# half-written output behind (BE-024). Paths are marked cancelled instead:
# running processes touching them are killed, and new ones refuse to start.
_procs_lock = threading.Lock()
_running: Dict[int, tuple] = {}          # pid -> (Popen, cmd)
_cancelled_paths: Dict[str, float] = {}  # path -> when it was cancelled
_CANCEL_MEMORY_SECONDS = 3600


def _touches(cmd: list, paths) -> bool:
    return any(p and p in str(arg) for arg in cmd for p in paths)


def cancel_ffmpeg_for(paths) -> int:
    """Kill ffmpeg processes whose command line mentions any of `paths`, and
    stop new ones that would. Returns how many were killed."""
    paths = [str(p) for p in paths if p]
    if not paths:
        return 0
    now = time.monotonic()
    killed = 0
    with _procs_lock:
        for p in [k for k, t in _cancelled_paths.items()
                  if now - t > _CANCEL_MEMORY_SECONDS]:
            del _cancelled_paths[p]
        for p in paths:
            _cancelled_paths[p] = now
        for proc, cmd in list(_running.values()):
            if _touches(cmd, paths) and proc.poll() is None:
                proc.kill()
                killed += 1
    if killed:
        app_logger.info(f"Stopped {killed} ffmpeg process(es) for a cancelled job")
    return killed


def run_ffmpeg(args: list, timeout: int = 3600) -> tuple:
    ffmpeg = get_ffmpeg_path() or "ffmpeg"
    cmd = [ffmpeg] + args

    app_logger.debug(f"FFmpeg: {' '.join(cmd)}")

    try:
        with _procs_lock:
            if _touches(cmd, _cancelled_paths):
                return -1, "", "FFmpeg cancelled"
            # encoding/errors: ffmpeg output can contain non-ASCII (titles,
            # paths); Windows' default cp1252 decode crashes on stray bytes.
            # stdin is closed so ffmpeg never waits on (or reads 'q' from) it.
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
            )
            _running[proc.pid] = (proc, cmd)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return -1, "", "FFmpeg timeout"
        finally:
            with _procs_lock:
                _running.pop(proc.pid, None)
        if proc.returncode != 0 and _touches(cmd, _cancelled_paths):
            return -1, stdout, "FFmpeg cancelled"
        return proc.returncode, stdout, stderr
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


def get_stream_signature(file_path: str) -> Optional[tuple]:
    """What has to match for files to be joined with -c copy: per stream, the
    type, codec, size, frame rate and sample rate/channels. None if unreadable."""
    import json
    ffprobe = get_ffprobe_path() or "ffprobe"
    cmd = [
        ffprobe, "-v", "quiet", "-print_format", "json",
        "-show_entries",
        "stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels"
        ":stream_disposition=attached_pic",
        file_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=30)
        if result.returncode != 0:
            return None
        streams = []
        for st in json.loads(result.stdout or '{}').get('streams', []):
            if (st.get('disposition') or {}).get('attached_pic'):
                continue  # embedded cover art, not part of the timeline
            if st.get('codec_type') not in ('video', 'audio'):
                continue
            streams.append((
                st.get('codec_type'), st.get('codec_name'),
                st.get('width'), st.get('height'), st.get('r_frame_rate'),
                st.get('sample_rate'), st.get('channels'),
            ))
        return tuple(streams) or None
    except Exception:
        return None


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
