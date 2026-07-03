import os
import tempfile
from typing import List, Dict
from pathlib import Path
from backend.utils.ffmpeg_handler import run_ffmpeg, get_media_duration
from backend.logger import app_logger


def _escape_meta_value(s: str) -> str:
    return (s.replace('\\', '\\\\')
             .replace('\n', '\\n')
             .replace('\r', '\\r')
             .replace('=', '\\='))


def generate_ffmpeg_metadata(
    tracks: List[Dict],
    output_path: str,
    overlap_seconds: float = 0.0,
) -> str:
    """Build an FFMETADATA chapter file.

    When tracks are crossfaded the timeline is pulled left by ``overlap_seconds``
    at every join, so each track (after the first) starts that much earlier than
    a naive cumulative sum. Chapter i spans [start_i, start_{i+1}); the final
    chapter runs to the true end of the (shortened) timeline.
    """
    overlap_ms = max(0, int(overlap_seconds * 1000))

    # Compute each track's start position on the (possibly crossfaded) timeline.
    starts: List[int] = []
    cursor = 0
    for i, track in enumerate(tracks):
        duration_ms = int((track.get('duration') or 0) * 1000)
        start_ms = 0 if i == 0 else max(0, cursor - overlap_ms)
        starts.append(start_ms)
        cursor = start_ms + duration_ms
    total_ms = cursor

    lines = [";FFMETADATA1\n"]
    for i, track in enumerate(tracks):
        start_ms = starts[i]
        end_ms = starts[i + 1] if i + 1 < len(starts) else total_ms

        title = track.get('title', f'Track {i+1}')
        artist = track.get('artist', '')

        lines.append(f"\n[CHAPTER]")
        lines.append(f"TIMEBASE=1/1000")
        lines.append(f"START={start_ms}")
        lines.append(f"END={end_ms}")
        lines.append(f"title={_escape_meta_value(title)}")
        if artist:
            lines.append(f"artist={_escape_meta_value(artist)}")

    meta_content = "\n".join(lines)

    meta_file = output_path + ".metadata.txt"
    with open(meta_file, 'w', encoding='utf-8') as f:
        f.write(meta_content)

    return meta_file


def embed_chapters(
    input_path: str,
    metadata_path: str,
    output_path: str
) -> bool:
    code, _, err = run_ffmpeg([
        '-i', input_path,
        '-i', metadata_path,
        '-map_metadata', '1',
        '-codec', 'copy',
        '-y',
        output_path
    ])

    if code != 0:
        app_logger.error(f"Chapter embed error: {err}")
        return False

    return True


def add_chapters_to_file(
    file_path: str,
    tracks: List[Dict],
    overlap_seconds: float = 0.0,
) -> bool:
    if not tracks:
        return True

    verified_tracks = []
    for track in tracks:
        if not track.get('duration') and track.get('file_path'):
            dur = get_media_duration(track['file_path'])
            track = {**track, 'duration': dur or 0}
        verified_tracks.append(track)

    meta_file = None
    temp_output = None
    replaced = False

    try:
        meta_file = generate_ffmpeg_metadata(verified_tracks, file_path, overlap_seconds)
        temp_output = file_path + ".chapters_temp" + Path(file_path).suffix

        if embed_chapters(file_path, meta_file, temp_output) and os.path.exists(temp_output):
            os.replace(temp_output, file_path)
            replaced = True
            app_logger.info(f"Chapters added to: {file_path}")
            return True
    except Exception as e:
        app_logger.error(f"Chapter addition error: {e}")
    finally:
        if meta_file and os.path.exists(meta_file):
            try:
                os.remove(meta_file)
            except Exception:
                pass
        if not replaced and temp_output and os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except Exception:
                pass

    return False
