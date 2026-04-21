import os
import tempfile
from typing import List, Dict
from pathlib import Path
from backend.utils.ffmpeg_handler import run_ffmpeg, get_media_duration
from backend.logger import app_logger


def generate_ffmpeg_metadata(
    tracks: List[Dict],
    output_path: str
) -> str:
    """
    Generate FFmpeg metadata file with chapter markers.
    tracks: list of {'title': str, 'artist': str, 'duration': float}
    Returns path to metadata file.
    """
    lines = [";FFMETADATA1\n"]

    cursor = 0
    for i, track in enumerate(tracks):
        duration_ms = int((track.get('duration') or 0) * 1000)
        start_ms = cursor
        end_ms = cursor + duration_ms

        title = track.get('title', f'Track {i+1}')
        artist = track.get('artist', '')

        lines.append(f"\n[CHAPTER]")
        lines.append(f"TIMEBASE=1/1000")
        lines.append(f"START={start_ms}")
        lines.append(f"END={end_ms}")
        lines.append(f"title={title}")
        if artist:
            lines.append(f"artist={artist}")

        cursor = end_ms

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
    """Embed chapter metadata into media file."""
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
    tracks: List[Dict]
) -> bool:
    """
    Add chapters to existing media file.
    Modifies file in place (creates temp then replaces).
    """
    if not tracks:
        return True

    # Verify durations from actual files if not provided
    verified_tracks = []
    for track in tracks:
        if not track.get('duration') and track.get('file_path'):
            dur = get_media_duration(track['file_path'])
            track = {**track, 'duration': dur or 0}
        verified_tracks.append(track)

    meta_file = None
    temp_output = None

    try:
        meta_file = generate_ffmpeg_metadata(verified_tracks, file_path)
        temp_output = file_path + ".chapters_temp" + Path(file_path).suffix

        success = embed_chapters(file_path, meta_file, temp_output)
        if success and os.path.exists(temp_output):
            os.replace(temp_output, file_path)
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
        if temp_output and os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except Exception:
                pass

    return False
