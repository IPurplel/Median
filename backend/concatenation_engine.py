import os
import asyncio
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Callable
from backend.utils.ffmpeg_handler import (
    run_ffmpeg, get_media_duration, validate_media_file, get_video_dimensions
)
from backend.ffmpeg_chapters_handler import add_chapters_to_file, generate_ffmpeg_metadata
from backend.image_processor import process_cover_image
from backend.config import settings
from backend.logger import app_logger


# ── Crossfade helpers ──────────────────────────────────────────────────────────
# Crossfade blends adjacent tracks instead of hard-cutting them. It is only used
# for the "Merge into single file" path and always forces a re-encode (the concat
# demuxer's -c copy fast path cannot overlap streams). These builders are pure so
# they can be unit-tested without ffmpeg.

def _crossfade_feasible(durations, crossfade_duration, max_tracks):
    """Return a usable crossfade duration, or None when crossfade isn't feasible.

    Infeasible when: fewer than 2 tracks, more than ``max_tracks`` (giant
    filtergraphs risk the OS command-length limit), any unknown/zero duration, or
    the shortest track is too short to overlap. Clamps to 0.9x the shortest track
    so every segment is longer than the overlap (keeps chapter offsets monotonic).
    """
    n = len(durations)
    if n < 2 or n > max_tracks:
        return None
    if any((d or 0) <= 0 for d in durations):
        return None
    d = min(crossfade_duration, 0.9 * min(durations))
    if d < 0.1:
        return None
    return round(d, 3)


def _audio_encoder_args(out_ext, bitrate=""):
    """ffmpeg audio-encoder args for a crossfaded (re-encoded) output by extension."""
    ext = str(out_ext).lower().lstrip('.')
    br = (bitrate or "").strip()
    if br and not br.lower().endswith('k'):
        br = f"{br}k"
    if ext == 'flac':
        return ['-c:a', 'flac']
    if ext == 'wav':
        return ['-c:a', 'pcm_s16le']
    if ext in ('ogg', 'opus', 'webm'):
        return ['-c:a', 'libopus', '-b:a', br or '192k']
    if ext in ('m4a', 'aac', 'mp4', 'mkv'):
        return ['-c:a', 'aac', '-b:a', br or settings.AUDIO_BITRATE_DEFAULT]
    return ['-c:a', 'libmp3lame', '-b:a', br or settings.AUDIO_BITRATE_DEFAULT]


def _build_acrossfade_filter(n, crossfade_duration, curve, sample_rate):
    """filter_complex for a chained constant-power audio crossfade over ``n`` inputs.

    Each input's audio is first conformed to a common sample rate / layout (mismatch
    makes acrossfade error or click). Returns (filter_string, output_label)."""
    parts = [
        f"[{i}:a]aformat=sample_rates={sample_rate}:channel_layouts=stereo[a{i}]"
        for i in range(n)
    ]
    prev = "a0"
    for i in range(1, n):
        label = "aout" if i == n - 1 else f"ax{i}"
        parts.append(
            f"[{prev}][a{i}]acrossfade=d={crossfade_duration}:c1={curve}:c2={curve}[{label}]"
        )
        prev = label
    return ";".join(parts), "aout"


def _build_xfade_filter(durations, crossfade_duration, transition, width, height, fps, scale_pad):
    """filter_complex for a chained video xfade over the inputs, synced to acrossfade.

    ``offset`` for the k-th join is measured on the accumulated first input and
    accounts for prior overlaps: offset_k = (Σ_{j<k} d_j) − k·D. When ``scale_pad``
    is set every clip is letterboxed onto a common W×H canvas (needed when input
    resolutions differ); otherwise only fps/SAR/pix_fmt are conformed so xfade
    accepts the streams. Returns (filter_string, output_label)."""
    n = len(durations)
    parts = []
    for i in range(n):
        if scale_pad:
            chain = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1,fps={fps},format=yuv420p"
            )
        else:
            chain = f"setsar=1,fps={fps},format=yuv420p"
        parts.append(f"[{i}:v]{chain}[v{i}]")

    prev = "v0"
    cumulative = 0.0
    for k in range(1, n):
        cumulative += durations[k - 1]
        offset = max(0.0, cumulative - k * crossfade_duration)
        label = "vout" if k == n - 1 else f"vx{k}"
        parts.append(
            f"[{prev}][v{k}]xfade=transition={transition}:"
            f"duration={crossfade_duration}:offset={offset:.3f}[{label}]"
        )
        prev = label
    return ";".join(parts), "vout"


def _safe_remove(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


def _write_concat_manifest(input_files, manifest_path):
    with open(manifest_path, 'w', encoding='utf-8') as f:
        for file_path in input_files:
            escaped = file_path.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")


def _collect_durations(input_files, tracks_meta=None):
    """Per-file durations (seconds), preferring track metadata, ffprobing otherwise."""
    durations = []
    for i, f in enumerate(input_files):
        d = 0
        if tracks_meta and i < len(tracks_meta):
            d = tracks_meta[i].get('duration') or 0
        if not d:
            d = get_media_duration(f) or 0
        durations.append(d)
    return durations


async def _run_hardcut_audio(loop, input_files, output_path):
    """Original fast path: concat demuxer + stream copy (no re-encode)."""
    manifest_path = output_path + ".concat_manifest.txt"
    try:
        _write_concat_manifest(input_files, manifest_path)

        def _concat():
            return run_ffmpeg([
                '-f', 'concat', '-safe', '0',
                '-i', manifest_path,
                '-c', 'copy',
                '-y', output_path
            ], timeout=settings.CONCAT_AUDIO_TIMEOUT)

        code, _, stderr = await loop.run_in_executor(None, _concat)
        if code != 0:
            app_logger.error(f"Audio concat error: {stderr}")
            return False
        return True
    finally:
        _safe_remove(manifest_path)


async def _run_crossfade_audio(loop, input_files, output_path, crossfade_duration, bitrate):
    """Crossfade path: chained acrossfade filter_complex + re-encode."""
    filt, aout = _build_acrossfade_filter(
        len(input_files), crossfade_duration,
        settings.CROSSFADE_CURVE, settings.CROSSFADE_NORMALIZE_SAMPLE_RATE
    )
    args = []
    for f in input_files:
        args += ['-i', f]
    args += ['-filter_complex', filt, '-map', f'[{aout}]']
    args += _audio_encoder_args(Path(output_path).suffix, bitrate)
    args += ['-y', output_path]

    def _run():
        return run_ffmpeg(args, timeout=settings.CONCAT_AUDIO_TIMEOUT)

    code, _, stderr = await loop.run_in_executor(None, _run)
    if code != 0:
        app_logger.error(f"Audio crossfade error: {stderr[-800:]}")
        return False
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        app_logger.error("Audio crossfade produced no output")
        return False
    return True


async def _merge_audio(input_files, output_path, tracks_meta,
                       crossfade=False, crossfade_duration=None, bitrate="",
                       progress_callback=None):
    """Merge audio tracks into output_path. Returns the overlap seconds used
    (0.0 for a hard cut) for chapter-offset correction, or None on failure."""
    if not input_files:
        return None

    if settings.CONCATENATION_VALIDATE_BEFORE:
        for f in input_files:
            if not validate_media_file(f):
                app_logger.error(f"Invalid media file: {f}")
                return None

    loop = asyncio.get_running_loop()

    effective = None
    if crossfade:
        cd = crossfade_duration if crossfade_duration is not None else settings.CROSSFADE_DURATION
        effective = _crossfade_feasible(
            _collect_durations(input_files, tracks_meta), cd, settings.CROSSFADE_MAX_TRACKS
        )
        if effective is None:
            app_logger.info("Crossfade not feasible for audio merge — using hard-cut concat")

    if effective is not None:
        if progress_callback:
            await progress_callback(10, "Crossfading audio...")
        if await _run_crossfade_audio(loop, input_files, output_path, effective, bitrate):
            return effective
        app_logger.warning("Audio crossfade failed — falling back to hard-cut concat")

    if progress_callback:
        await progress_callback(10, "Concatenating audio...")
    if await _run_hardcut_audio(loop, input_files, output_path):
        return 0.0
    return None


async def concatenate_audio(
    input_files: List[str],
    output_path: str,
    tracks_meta: List[Dict],
    add_chapters: bool = True,
    progress_callback: Optional[Callable] = None,
    crossfade: bool = False,
    crossfade_duration: Optional[float] = None,
    bitrate: str = "",
) -> bool:
    if not input_files:
        return False

    try:
        overlap = await _merge_audio(
            input_files, output_path, tracks_meta,
            crossfade=crossfade, crossfade_duration=crossfade_duration,
            bitrate=bitrate, progress_callback=progress_callback,
        )
        if overlap is None:
            return False

        if progress_callback:
            await progress_callback(80, "Adding chapter markers...")

        if add_chapters and settings.CONCATENATION_CREATE_CHAPTERS and tracks_meta:
            loop = asyncio.get_running_loop()
            for i, (track, file_path) in enumerate(zip(tracks_meta, input_files)):
                dur = track.get('duration') or 0
                if not dur:
                    dur = get_media_duration(file_path) or 0
                    tracks_meta[i]['duration'] = dur
                # Zero-duration tracks produce degenerate chapter markers; warn and skip
                if dur == 0:
                    app_logger.warning(
                        f"Track {i+1} ({track.get('title', '?')!r}) has zero duration — "
                        "its chapter marker will be a single point in time"
                    )

            await loop.run_in_executor(
                None, add_chapters_to_file, output_path, tracks_meta, overlap
            )

        if progress_callback:
            await progress_callback(100, "Complete")

        return True

    except Exception as e:
        app_logger.error(f"Concatenation error: {e}")
        return False


async def _run_crossfade_video(loop, input_files, output_path, crossfade_duration):
    """Crossfade path for video: xfade (video) + acrossfade (audio) in one
    filter_complex so the two stay locked in sync (both shorten by D per join).

    Resolution rule: if every clip already shares the exact same resolution, keep
    it as the canvas and skip scale/pad; otherwise normalise all clips onto a fixed
    1080p canvas with aspect-preserving letterbox/pillarbox."""
    durations = _collect_durations(input_files)
    dims = [get_video_dimensions(f) for f in input_files]

    if all(d is not None for d in dims) and len(set(dims)) == 1:
        width, height = dims[0]
        scale_pad = False
        app_logger.info(f"Video crossfade: uniform {width}x{height}, native canvas")
    else:
        width = settings.CROSSFADE_FALLBACK_WIDTH
        height = settings.CROSSFADE_FALLBACK_HEIGHT
        scale_pad = True
        app_logger.info(f"Video crossfade: mixed resolutions {dims} — normalising to {width}x{height}")

    vfilt, vout = _build_xfade_filter(
        durations, crossfade_duration, settings.CROSSFADE_VIDEO_TRANSITION,
        width, height, settings.CROSSFADE_VIDEO_FPS, scale_pad
    )
    afilt, aout = _build_acrossfade_filter(
        len(input_files), crossfade_duration,
        settings.CROSSFADE_CURVE, settings.CROSSFADE_NORMALIZE_SAMPLE_RATE
    )
    filter_complex = f"{vfilt};{afilt}"

    args = []
    for f in input_files:
        args += ['-i', f]
    args += [
        '-filter_complex', filter_complex,
        '-map', f'[{vout}]', '-map', f'[{aout}]',
        '-c:v', settings.VIDEO_CODEC_H264, '-preset', settings.VIDEO_PRESET,
        '-crf', str(settings.VIDEO_CRF),
        '-c:a', settings.AUDIO_CODEC_AAC, '-b:a', settings.AUDIO_BITRATE_DEFAULT,
        '-pix_fmt', 'yuv420p',
        '-y', output_path,
    ]

    def _run():
        return run_ffmpeg(args, timeout=settings.CONCAT_VIDEO_TIMEOUT)

    code, _, stderr = await loop.run_in_executor(None, _run)
    if code != 0:
        app_logger.error(f"Video crossfade error: {stderr[-800:]}")
        return False
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        app_logger.error("Video crossfade produced no output")
        return False
    return True


async def concatenate_video(
    input_files: List[str],
    output_path: str,
    output_format: str = "mp4",
    progress_callback: Optional[Callable] = None,
    crossfade: bool = False,
    crossfade_duration: Optional[float] = None,
) -> bool:
    if not input_files:
        return False

    loop = asyncio.get_running_loop()

    if crossfade:
        cd = crossfade_duration if crossfade_duration is not None else settings.CROSSFADE_DURATION
        effective = _crossfade_feasible(
            _collect_durations(input_files), cd, settings.CROSSFADE_MAX_TRACKS
        )
        if effective is None:
            app_logger.info("Crossfade not feasible for video merge — using hard-cut concat")
        else:
            if progress_callback:
                await progress_callback(10, "Crossfading video...")
            if await _run_crossfade_video(loop, input_files, output_path, effective):
                if progress_callback:
                    await progress_callback(100, "Complete")
                return True
            app_logger.warning("Video crossfade failed — falling back to hard-cut concat")

    manifest_path = output_path + ".video_manifest.txt"
    try:
        _write_concat_manifest(input_files, manifest_path)

        if progress_callback:
            await progress_callback(10, "Concatenating video...")

        def _concat():
            return run_ffmpeg([
                '-f', 'concat',
                '-safe', '0',
                '-i', manifest_path,
                '-c:v', settings.VIDEO_CODEC_H264,
                '-preset', settings.VIDEO_PRESET,
                '-c:a', settings.AUDIO_CODEC_AAC,
                '-y',
                output_path
            ], timeout=settings.CONCAT_VIDEO_TIMEOUT)

        code, _, stderr = await loop.run_in_executor(None, _concat)

        if code != 0:
            app_logger.error(f"Video concat error: {stderr}")
            return False

        if progress_callback:
            await progress_callback(100, "Complete")

        return True

    except Exception as e:
        app_logger.error(f"Video concatenation error: {e}")
        return False
    finally:
        _safe_remove(manifest_path)


def _metadata_args(album_meta: Optional[Dict]) -> List[str]:
    """Translate Median's album_meta dict into ffmpeg -metadata flags.

    Empty values are skipped so we don't clobber whatever ffmpeg-copied
    container metadata might already have."""
    if not album_meta:
        return []
    args: List[str] = []
    mapping = (
        ('title', 'title'),
        ('artist', 'artist'),
        ('album', 'album'),
        ('year', 'date'),
        ('genre', 'genre'),
    )
    for key, ffmpeg_key in mapping:
        value = (album_meta.get(key) or '').strip()
        if value:
            args += ['-metadata', f'{ffmpeg_key}={value}']
    return args


def _matroska_attachment_args(cover_path: str) -> List[str]:
    """ffmpeg args to attach the cover as a Matroska 'cover.jpg' attachment.

    AIMP, MusicBee, foobar2000 and other music players display the cover by
    looking up an attachment whose filename starts with 'cover'. The cover
    video stream alone is not recognised as album art."""
    if not cover_path or not os.path.exists(cover_path):
        return []
    ext = os.path.splitext(cover_path)[1].lower()
    if ext == '.png':
        mime, name = 'image/png', 'cover.png'
    elif ext in ('.jpg', '.jpeg'):
        mime, name = 'image/jpeg', 'cover.jpg'
    else:
        # WebP/GIF/etc — Matroska players reliably handle only jpeg/png, but
        # ffmpeg will still mux it; let the player decide.
        mime, name = f'image/{ext.lstrip(".") or "jpeg"}', f'cover{ext or ".jpg"}'
    return [
        '-attach', cover_path,
        '-metadata:s:t', f'mimetype={mime}',
        '-metadata:s:t', f'filename={name}',
    ]


async def create_cover_audio_video(
    audio_files: List[str],
    cover_path: str,
    output_path: str,
    tracks_meta: List[Dict],
    cover_ratio: str = "1:1",
    cover_resolution: str = "original",
    add_chapters: bool = True,
    progress_callback: Optional[Callable] = None,
    album_meta: Optional[Dict] = None,
    crossfade: bool = False,
    crossfade_duration: Optional[float] = None,
) -> bool:
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

    if progress_callback:
        await progress_callback(20, "Preparing audio...")

    # Cover+Audio renders one still image over the whole timeline, so the crossfade
    # only affects the audio pre-merge here; -shortest below clamps the looped image
    # to the (now shorter) merged audio automatically. audio_overlap is captured so
    # the chapter markers land on the shortened timeline.
    temp_audio_created = False
    audio_overlap = 0.0
    if len(audio_files) == 1:
        audio_for_merge = audio_files[0]
    else:
        ext = Path(audio_files[0]).suffix
        audio_for_merge = output_path + f".temp_audio{ext}"
        temp_audio_created = True

        audio_overlap = await _merge_audio(
            audio_files, audio_for_merge, tracks_meta,
            crossfade=crossfade, crossfade_duration=crossfade_duration,
        )
        if audio_overlap is None:
            app_logger.error("Audio concatenation failed in cover+audio merge")
            return False

    if progress_callback:
        await progress_callback(50, "Merging cover with audio...")

    try:
        def _merge():
            out_ext = Path(output_path).suffix.lower().lstrip('.')

            args = [
                '-loop', '1',
                '-i', processed_cover,
                '-i', audio_for_merge,
            ]

            meta_args = _metadata_args(album_meta)

            if out_ext == 'webm':
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
                    '-b:v', '0', '-crf', str(settings.VIDEO_CRF),
                    '-c:a', 'libopus',
                ]
                args += [
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                    '-pix_fmt', 'yuv420p',
                    '-map', '0:v:0',
                    '-map', '1:a:0',
                    '-shortest',
                    *meta_args,
                    '-y',
                    output_path,
                ]
            elif out_ext == 'mkv':
                args += [
                    '-c:v', settings.VIDEO_CODEC_H264,
                    '-preset', settings.VIDEO_PRESET,
                    '-tune', settings.VIDEO_TUNE_STILL,
                    '-crf', str(settings.VIDEO_CRF),
                    '-c:a', settings.AUDIO_CODEC_AAC,
                    '-b:a', settings.AUDIO_BITRATE_DEFAULT,
                ]
                args += [
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                    '-pix_fmt', 'yuv420p',
                    '-map', '0:v:0',
                    '-map', '1:a:0',
                    '-shortest',
                    *_matroska_attachment_args(processed_cover),
                    *meta_args,
                    '-y',
                    output_path,
                ]
            else:
                args += [
                    '-c:v', settings.VIDEO_CODEC_H264,
                    '-preset', settings.VIDEO_PRESET,
                    '-tune', settings.VIDEO_TUNE_STILL,
                    '-crf', str(settings.VIDEO_CRF),
                    '-c:a', settings.AUDIO_CODEC_AAC,
                    '-b:a', settings.AUDIO_BITRATE_DEFAULT,
                ]
                args += [
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
                    '-pix_fmt', 'yuv420p',
                    '-map', '0:v:0',
                    '-map', '1:a:0',
                    '-shortest',
                    *meta_args,
                    '-movflags', '+faststart',
                    '-y',
                    output_path,
                ]

            app_logger.info(f"FFmpeg merge: {' '.join(args)}")
            return run_ffmpeg(args, timeout=settings.COVER_MERGE_TIMEOUT)

        code, stdout, stderr = await loop.run_in_executor(None, _merge)

        if code != 0:
            app_logger.error(f"Cover+audio FFmpeg merge failed (exit {code}):\n{stderr[-800:]}")
            return False

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            app_logger.error(f"Merge produced no output at {output_path}")
            return False

        app_logger.info(f"Cover+audio merge OK: {output_path} ({os.path.getsize(output_path)} bytes)")

    finally:
        if temp_audio_created and os.path.exists(audio_for_merge):
            try:
                os.remove(audio_for_merge)
            except Exception:
                pass

    if add_chapters and len(tracks_meta) > 1:
        if progress_callback:
            await progress_callback(90, "Adding chapter markers...")
        try:
            for i, (track, af) in enumerate(zip(tracks_meta, audio_files)):
                if not track.get('duration'):
                    dur = get_media_duration(af) or 0
                    tracks_meta[i]['duration'] = dur
            await loop.run_in_executor(
                None, add_chapters_to_file, output_path, tracks_meta, audio_overlap
            )
        except Exception as e:
            app_logger.warning(f"Chapter embedding failed (non-fatal): {e}")

    if progress_callback:
        await progress_callback(100, "Complete")

    return True
