import os
import asyncio
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Callable
from backend.utils.ffmpeg_handler import run_ffmpeg, get_media_duration, validate_media_file
from backend.ffmpeg_chapters_handler import add_chapters_to_file, generate_ffmpeg_metadata
from backend.image_processor import process_cover_image
from backend.config import settings
from backend.logger import app_logger


async def concatenate_audio(
    input_files: List[str],
    output_path: str,
    tracks_meta: List[Dict],
    add_chapters: bool = True,
    progress_callback: Optional[Callable] = None
) -> bool:
    """
    Concatenate audio files using FFmpeg concat demuxer (no re-encoding).
    """
    if not input_files:
        return False

    if settings.CONCATENATION_VALIDATE_BEFORE:
        for f in input_files:
            if not validate_media_file(f):
                app_logger.error(f"Invalid media file: {f}")
                return False

    # Create manifest file
    manifest_path = output_path + ".concat_manifest.txt"
    try:
        with open(manifest_path, 'w', encoding='utf-8') as f:
            for file_path in input_files:
                escaped = file_path.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        if progress_callback:
            await progress_callback(10, "Concatenating audio...")

        loop = asyncio.get_running_loop()

        def _concat():
            return run_ffmpeg([
                '-f', 'concat',
                '-safe', '0',
                '-i', manifest_path,
                '-c', 'copy',
                '-y',
                output_path
            ])

        code, stdout, stderr = await loop.run_in_executor(None, _concat)

        if code != 0:
            app_logger.error(f"Audio concat error: {stderr}")
            return False

        if progress_callback:
            await progress_callback(80, "Adding chapter markers...")

        # Add chapters
        if add_chapters and settings.CONCATENATION_CREATE_CHAPTERS and tracks_meta:
            # Verify/get durations
            for i, (track, file_path) in enumerate(zip(tracks_meta, input_files)):
                if not track.get('duration'):
                    dur = get_media_duration(file_path)
                    tracks_meta[i]['duration'] = dur or 0

            await loop.run_in_executor(None, add_chapters_to_file, output_path, tracks_meta)

        if progress_callback:
            await progress_callback(100, "Complete")

        return True

    except Exception as e:
        app_logger.error(f"Concatenation error: {e}")
        return False
    finally:
        if os.path.exists(manifest_path):
            os.remove(manifest_path)


async def concatenate_video(
    input_files: List[str],
    output_path: str,
    output_format: str = "mp4",
    progress_callback: Optional[Callable] = None
) -> bool:
    """
    Concatenate video files with re-encoding for consistency.
    """
    if not input_files:
        return False

    manifest_path = output_path + ".video_manifest.txt"
    try:
        with open(manifest_path, 'w', encoding='utf-8') as f:
            for fp in input_files:
                escaped = fp.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        if progress_callback:
            await progress_callback(10, "Concatenating video...")

        loop = asyncio.get_running_loop()

        def _concat():
            return run_ffmpeg([
                '-f', 'concat',
                '-safe', '0',
                '-i', manifest_path,
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-c:a', 'aac',
                '-y',
                output_path
            ])

        code, _, stderr = await loop.run_in_executor(None, _concat)

        if code != 0:
            app_logger.error(f"Video concat error: {stderr}")
            return False

        if progress_callback:
            await progress_callback(100, "Complete")

        return True

    finally:
        if os.path.exists(manifest_path):
            os.remove(manifest_path)


async def create_cover_audio_video(
    audio_files: List[str],
    cover_path: str,
    output_path: str,
    tracks_meta: List[Dict],
    cover_ratio: str = "1:1",
    cover_resolution: str = "medium",
    add_chapters: bool = True,
    progress_callback: Optional[Callable] = None
) -> bool:
    """
    Merge a static cover image with audio into a single video file.
    For a single audio file: image + audio → one MP4/MKV/WebM directly.
    For multiple audio files: concat audio first, then merge with cover.
    """
    if not audio_files or not cover_path:
        app_logger.error("create_cover_audio_video: missing audio_files or cover_path")
        return False

    if not os.path.exists(cover_path):
        app_logger.error(f"Cover image not found: {cover_path}")
        return False

    for af in audio_files:
        if not os.path.exists(af):
            app_logger.error(f"Audio file not found: {af}")
            return False

    loop = asyncio.get_running_loop()

    # ── Step 1: Process cover image ───────────────────────────────────────
    if progress_callback:
        await progress_callback(5, "Processing cover image...")

    try:
        processed_cover = await process_cover_image(cover_path, cover_ratio, cover_resolution)
        if not processed_cover or not os.path.exists(processed_cover):
            app_logger.warning("Cover image processing failed — using original")
            processed_cover = cover_path
    except Exception as e:
        app_logger.warning(f"Cover processing error ({e}) — using original image")
        processed_cover = cover_path

    # ── Step 2: Prepare audio ─────────────────────────────────────────────
    if progress_callback:
        await progress_callback(20, "Preparing audio...")

    if len(audio_files) == 1:
        # Single file — use it directly, no concat needed
        audio_for_merge = audio_files[0]
        temp_audio_created = False
    else:
        # Multiple files — concat first
        ext = Path(audio_files[0]).suffix
        audio_for_merge = output_path + f".temp_audio{ext}"
        temp_audio_created = True

        audio_ok = await concatenate_audio(
            audio_files, audio_for_merge, tracks_meta, add_chapters=False
        )
        if not audio_ok:
            app_logger.error("Audio concatenation failed in cover+audio merge")
            return False

    # ── Step 3: Merge cover + audio ───────────────────────────────────────
    if progress_callback:
        await progress_callback(50, "Merging cover with audio...")

    try:
        def _merge():
            out_ext = Path(output_path).suffix.lower().lstrip('.')

            args = [
                '-loop', '1',            # loop still image
                '-i', processed_cover,   # input 0 → video stream
                '-i', audio_for_merge,   # input 1 → audio stream
            ]

            if out_ext == 'webm':
                # Try libvpx-vp9 first (better quality); fall back to libvpx (vp8).
                # Note: -movflags +faststart is not supported in WebM — omitted here.
                import subprocess as _sp
                vp9_available = False
                try:
                    r = _sp.run(
                        ['ffmpeg', '-encoders'], capture_output=True, text=True, timeout=5
                    )
                    vp9_available = 'libvpx-vp9' in r.stdout
                except Exception:
                    pass
                vcodec = 'libvpx-vp9' if vp9_available else 'libvpx'
                args += [
                    '-c:v', vcodec,
                    '-b:v', '0', '-crf', '18',
                    '-c:a', 'libopus',
                ]
                args += [
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                    '-pix_fmt', 'yuv420p',
                    '-map', '0:v:0',
                    '-map', '1:a:0',
                    '-shortest',
                    '-y',
                    output_path,
                ]
            elif out_ext == 'mkv':
                args += [
                    '-c:v', 'libx264',
                    '-preset', 'fast',
                    '-tune', 'stillimage',
                    '-crf', '18',
                    '-c:a', 'aac',
                    '-b:a', '192k',
                ]
                args += [
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                    '-pix_fmt', 'yuv420p',
                    '-map', '0:v:0',
                    '-map', '1:a:0',
                    '-shortest',
                    '-y',
                    output_path,
                ]
            else:
                # MP4 — widest player compatibility
                args += [
                    '-c:v', 'libx264',
                    '-preset', 'fast',
                    '-tune', 'stillimage',
                    '-crf', '18',
                    '-c:a', 'aac',
                    '-b:a', '192k',
                ]
                args += [
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                    '-pix_fmt', 'yuv420p',
                    '-map', '0:v:0',
                    '-map', '1:a:0',
                    '-shortest',
                    '-movflags', '+faststart',
                    '-y',
                    output_path,
                ]

            app_logger.info(f"FFmpeg merge: {' '.join(args)}")
            return run_ffmpeg(args)

        code, stdout, stderr = await loop.run_in_executor(None, _merge)

        if code != 0:
            app_logger.error(f"Cover+audio FFmpeg merge failed (exit {code}):\n{stderr[-800:]}")
            return False

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            app_logger.error(f"Merge produced no output at {output_path}")
            return False

        app_logger.info(f"Cover+audio merge OK: {output_path} ({os.path.getsize(output_path)} bytes)")

    finally:
        # Clean up temp concat file if we created one
        if temp_audio_created and os.path.exists(audio_for_merge):
            try:
                os.remove(audio_for_merge)
            except Exception:
                pass

    # ── Step 4: Embed chapters (multi-track only) ─────────────────────────
    if add_chapters and len(tracks_meta) > 1:
        if progress_callback:
            await progress_callback(90, "Adding chapter markers...")
        try:
            for i, (track, af) in enumerate(zip(tracks_meta, audio_files)):
                if not track.get('duration'):
                    dur = get_media_duration(af)
                    tracks_meta[i]['duration'] = dur or 0
            await loop.run_in_executor(None, add_chapters_to_file, output_path, tracks_meta)
        except Exception as e:
            app_logger.warning(f"Chapter embedding failed (non-fatal): {e}")

    if progress_callback:
        await progress_callback(100, "Complete")

    return True
