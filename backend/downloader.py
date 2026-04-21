import asyncio
import os
import uuid
import json
import time
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from backend.config import settings
from backend.utils.validators import detect_platform, is_playlist_url
from backend.utils.file_organizer import (
    get_single_track_filename, get_album_filename,
    get_playlist_folder, get_track_in_album_filename,
    ensure_unique_path
)
from backend.concatenation_engine import (
    concatenate_audio, concatenate_video, create_cover_audio_video
)
from backend.image_processor import download_cover_image
from backend.logger import app_logger


FORMAT_EXT_MAP = {
    # Audio
    'mp3': 'mp3', 'flac': 'flac', 'aac': 'm4a',
    # Video
    'mp4': 'mp4', 'mkv': 'mkv', 'webm': 'webm',
}

AUDIO_FORMATS = {'mp3', 'flac', 'aac'}
VIDEO_FORMATS = {'mp4', 'mkv', 'webm'}


def _get_ydl_opts(
    download_type: str,
    fmt: str,
    bitrate: str,
    output_template: str,
    progress_hook: Optional[Callable] = None
) -> dict:
    """Build yt-dlp options dict."""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'outtmpl': output_template,
        'socket_timeout': 60,
        'retries': 3,
        'fragment_retries': 3,
        'concurrent_fragment_downloads': 4,
        'http_chunk_size': settings.DOWNLOAD_CHUNK_SIZE * 1024 * 1024,
    }

    if progress_hook:
        opts['progress_hooks'] = [progress_hook]

    # Normalize bitrate string to digits only
    bitrate_val = (bitrate or '').replace('kbps', '').strip() if bitrate else ''

    if download_type == 'audio':
        opts['format'] = 'bestaudio/best'
        if fmt == 'flac':
            opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'flac'}]
        elif fmt == 'mp3':
            pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}
            if bitrate_val:
                pp['preferredquality'] = bitrate_val
            opts['postprocessors'] = [pp]
        elif fmt in ('aac', 'm4a'):
            pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': 'aac'}
            if bitrate_val:
                pp['preferredquality'] = bitrate_val
            opts['postprocessors'] = [pp]
        else:
            pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}
            if bitrate_val:
                pp['preferredquality'] = bitrate_val
            opts['postprocessors'] = [pp]

    elif download_type == 'video':
        # Choose format based on container
        if fmt == 'webm':
            if bitrate_val:
                opts['format'] = f'bestvideo[ext=webm]+bestaudio[ext=webm][abr<={bitrate_val}]/bestvideo[ext=webm]+bestaudio[ext=webm]/best[ext=webm]/best'
            else:
                opts['format'] = 'bestvideo[ext=webm]+bestaudio[ext=webm]/best[ext=webm]/best'
        elif fmt == 'mkv':
            if bitrate_val:
                opts['format'] = f'bestvideo+bestaudio[abr<={bitrate_val}]/bestvideo+bestaudio/best'
            else:
                opts['format'] = 'bestvideo+bestaudio/best'
            opts['merge_output_format'] = 'mkv'
        else:  # mp4
            if bitrate_val:
                opts['format'] = f'bestvideo[ext=mp4]+bestaudio[ext=mp4][abr<={bitrate_val}]/bestvideo[ext=mp4]+bestaudio[ext=mp4]/best[ext=mp4]/best'
            else:
                opts['format'] = 'bestvideo[ext=mp4]+bestaudio[ext=mp4]/best[ext=mp4]/best'
            opts['merge_output_format'] = 'mp4'

    elif download_type == 'cover_audio':
        # Download best audio + thumbnail; audio will be merged with cover image
        opts['format'] = 'bestaudio/best'
        pp = {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}
        if bitrate_val:
            pp['preferredquality'] = bitrate_val
        opts['postprocessors'] = [pp]
        # Always download the thumbnail alongside the audio
        opts['writethumbnail'] = True
        opts['postprocessors'].append({'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'})

    # Explicitly disable thumbnail writing for all types except cover_audio
    # to prevent .jpg/.webp files from appearing alongside audio in the download folder
    if download_type != 'cover_audio':
        opts['writethumbnail'] = False

    return opts


async def download_single(
    url: str,
    download_type: str,
    fmt: str,
    bitrate: str,
    metadata: dict,
    progress_callback: Optional[Callable] = None,
    cover_settings: Optional[dict] = None
) -> Dict[str, Any]:
    """Download a single track."""
    import yt_dlp

    download_dir = Path(settings.UPLOAD_FOLDER)
    download_dir.mkdir(parents=True, exist_ok=True)

    artist = metadata.get('artist') or 'Unknown Artist'
    title = metadata.get('title') or 'Unknown Title'
    # The audio is always downloaded as mp3 internally; the final file uses the
    # output_format from cover_settings (mp4/mkv/webm).
    if download_type == 'cover_audio':
        ext = (cover_settings or {}).get('output_format', 'mp4')
    else:
        ext = FORMAT_EXT_MAP.get(fmt, fmt)

    filename = get_single_track_filename(artist, title, ext)
    output_path = ensure_unique_path(download_dir / filename)
    temp_template = str(download_dir / f"_tmp_{uuid.uuid4().hex}")

    last_progress = {'pct': 0, 'speed': '', 'eta': ''}

    # Capture the event loop BEFORE entering the thread executor
    main_loop = asyncio.get_running_loop()

    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
            downloaded = d.get('downloaded_bytes', 0)
            pct = min(90, (downloaded / total) * 90)
            speed = d.get('_speed_str', '').strip()
            eta = d.get('_eta_str', '').strip()
            last_progress.update({'pct': pct, 'speed': speed, 'eta': eta})

            # Progress hook runs in a thread — must use run_coroutine_threadsafe,
            # NOT asyncio.create_task() which requires the calling thread to own
            # a running event loop.
            if progress_callback and main_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    progress_callback(pct, f"Downloading... {speed}"),
                    main_loop
                )

    ydl_opts = _get_ydl_opts(download_type, fmt, bitrate, temp_template + '.%(ext)s', hook)

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    await asyncio.get_running_loop().run_in_executor(None, _download)

    # For cover_audio, audio is extracted as mp3 regardless of requested fmt
    audio_ext = 'mp3' if download_type == 'cover_audio' else ext

    # Find downloaded audio file
    downloaded_file = _find_downloaded_file(temp_template, audio_ext)

    if not downloaded_file:
        raise FileNotFoundError(f"Download failed: no output file found for {url}")

    if download_type == 'cover_audio':
        # ── Find the cover image ──────────────────────────────────────────
        cover_file = None
        parent_dir = Path(temp_template).parent
        stem = Path(temp_template).name

        # 1. Direct match alongside the audio file
        for img_ext in ('jpg', 'jpeg', 'png', 'webp'):
            candidate = _find_downloaded_file(temp_template, img_ext)
            if candidate and os.path.exists(candidate):
                cover_file = candidate
                app_logger.info(f"Cover found (direct): {candidate}")
                break

        # 2. Any image in the same directory that shares the temp stem
        if not cover_file and parent_dir.exists():
            for f in sorted(parent_dir.iterdir()):
                if (f.stem.startswith(stem) and
                        f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') and
                        f.stat().st_size > 0):
                    cover_file = str(f)
                    app_logger.info(f"Cover found (scan): {cover_file}")
                    break

        # 3. Download thumbnail from metadata URL as last resort
        if not cover_file and metadata.get('thumbnail'):
            fallback_cover = temp_template + '_cover.jpg'
            app_logger.info(f"Downloading cover from metadata thumbnail URL...")
            cover_file = await download_cover_image(metadata['thumbnail'], fallback_cover)
            if cover_file:
                app_logger.info(f"Cover downloaded from URL: {cover_file}")

        if not cover_file or not os.path.exists(cover_file):
            app_logger.error(
                f"No cover image found for cover+audio download. "
                f"Searched dir: {parent_dir}, stem: {stem}. "
                f"Files present: {[f.name for f in parent_dir.iterdir()] if parent_dir.exists() else '[]'}"
            )
            # Clean up audio and raise — don't silently produce audio-only
            if os.path.exists(downloaded_file):
                os.remove(downloaded_file)
            raise RuntimeError(
                "Cover image not found. yt-dlp may not have downloaded the thumbnail. "
                "Try a different URL or check if the platform supports thumbnail download."
            )

        # ── Merge cover + audio into single video file ────────────────────
        cs = cover_settings or {}
        out_ext = cs.get('output_format', 'mp4')
        video_output = ensure_unique_path(
            download_dir / get_single_track_filename(artist, title, out_ext)
        )

        app_logger.info(
            f"Merging cover ({cover_file}) + audio ({downloaded_file}) → {video_output}"
        )

        if progress_callback:
            await progress_callback(92, "Merging cover with audio...")

        ok = await create_cover_audio_video(
            audio_files=[downloaded_file],
            cover_path=cover_file,
            output_path=str(video_output),
            tracks_meta=[{
                'title': title,
                'artist': artist,
                'duration': metadata.get('duration') or 0,
            }],
            cover_ratio=cs.get('ratio', '1:1'),
            cover_resolution=cs.get('resolution', 'medium'),
            add_chapters=False,
        )

        # Clean up source audio + cover after merge attempt
        for tmp_f in [downloaded_file, cover_file]:
            if tmp_f and os.path.exists(tmp_f):
                try:
                    os.remove(tmp_f)
                except Exception:
                    pass

        if ok and video_output.exists() and video_output.stat().st_size > 0:
            final_path = str(video_output)
        else:
            raise RuntimeError(
                "Cover+audio merge failed. Check FFmpeg is installed and the audio/image are valid."
            )
    else:
        os.rename(downloaded_file, str(output_path))
        final_path = str(output_path)

    file_size = os.path.getsize(final_path) if os.path.exists(final_path) else 0

    if progress_callback:
        await progress_callback(100, "Complete")

    return {
        'file_path': final_path,
        'file_size': file_size,
        'title': title,
        'artist': artist,
    }


async def download_playlist(
    url: str,
    download_type: str,
    fmt: str,
    bitrate: str,
    metadata: dict,
    concatenate: bool = False,
    progress_callback: Optional[Callable] = None,
    cover_settings: Optional[dict] = None
) -> Dict[str, Any]:
    """Download a playlist or album."""
    import yt_dlp

    download_dir = Path(settings.UPLOAD_FOLDER)
    artist = metadata.get('artist') or 'Unknown Artist'
    album  = metadata.get('album') or metadata.get('title') or 'Unknown Album'
    tracks = metadata.get('tracks', [])
    track_count = metadata.get('track_count', len(tracks))

    # For cover_audio the final container is the video format from cover_settings
    if download_type == 'cover_audio':
        ext = (cover_settings or {}).get('output_format', 'mp4')
    else:
        ext = FORMAT_EXT_MAP.get(fmt, fmt)

    if concatenate:
        # Download all tracks to temp, then concat
        temp_dir = download_dir / f"_concat_{uuid.uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        downloaded_files = []
        downloaded_covers = []

        try:
            for i, track in enumerate(tracks):
                track_url = track.get('url', '')
                if not track_url:
                    continue

                pct_base = (i / max(track_count, 1)) * 70

                if progress_callback:
                    await progress_callback(
                        pct_base,
                        f"Downloading track {i+1}/{track_count}..."
                    )

                temp_template = str(temp_dir / f"track_{i:03d}")

                if download_type == 'cover_audio':
                    # Download audio as mp3 + thumbnail for each track
                    ydl_opts = _get_ydl_opts(
                        'cover_audio', 'mp3', bitrate,
                        temp_template + '.%(ext)s'
                    )
                else:
                    ydl_opts = _get_ydl_opts(
                        download_type, fmt, bitrate,
                        temp_template + '.%(ext)s'
                    )

                loop = asyncio.get_running_loop()

                def _dl(u=track_url, o=ydl_opts):
                    with yt_dlp.YoutubeDL(o) as ydl:
                        ydl.download([u])

                await loop.run_in_executor(None, _dl)

                dl_ext = 'mp3' if download_type == 'cover_audio' else ext
                f = _find_downloaded_file(temp_template, dl_ext)
                if f:
                    downloaded_files.append(f)
                    if not track.get('duration'):
                        from backend.utils.ffmpeg_handler import get_media_duration
                        dur = get_media_duration(f)
                        tracks[i]['duration'] = dur or 0

                if download_type == 'cover_audio':
                    for img_ext in ('jpg', 'png', 'webp'):
                        cf = _find_downloaded_file(temp_template, img_ext)
                        if cf:
                            downloaded_covers.append(cf)
                            break

            if not downloaded_files:
                raise ValueError("No tracks downloaded")

            if progress_callback:
                await progress_callback(75, "Concatenating...")

            output_filename = get_album_filename(artist, album, ext)
            output_path = ensure_unique_path(download_dir / output_filename)

            if download_type == 'audio':
                ok = await concatenate_audio(
                    downloaded_files, str(output_path), tracks,
                    add_chapters=True, progress_callback=progress_callback
                )
            elif download_type == 'video':
                ok = await concatenate_video(
                    downloaded_files, str(output_path), fmt,
                    progress_callback=progress_callback
                )
            elif download_type == 'cover_audio':
                cover_file = downloaded_covers[0] if downloaded_covers else None
                if not cover_file:
                    cover_url = metadata.get('thumbnail', '')
                    if cover_url:
                        # Bug fix: only set cover_file AFTER confirming download succeeded
                        fallback_path = str(temp_dir / 'album_cover.jpg')
                        result_path = await download_cover_image(cover_url, fallback_path)
                        if result_path and os.path.exists(result_path):
                            cover_file = result_path
                        else:
                            cover_file = None  # download failed, keep None

                ok = await create_cover_audio_video(
                    downloaded_files,
                    cover_file or '',
                    str(output_path),
                    tracks,
                    cover_ratio=cover_settings.get('ratio', '1:1') if cover_settings else '1:1',
                    cover_resolution=cover_settings.get('resolution', 'medium') if cover_settings else 'medium',
                    add_chapters=True,
                    progress_callback=progress_callback
                )
            else:
                ok = False

            if not ok:
                raise RuntimeError("Concatenation failed")

            file_size = os.path.getsize(str(output_path)) if output_path.exists() else 0

            return {
                'file_path': str(output_path),
                'file_size': file_size,
                'track_count': len(downloaded_files),
                'artist': artist,
                'album': album,
            }

        finally:
            # Cleanup temp files
            import shutil
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    else:
        # Individual files in album folder
        album_folder = download_dir / get_playlist_folder(artist, album)
        album_folder.mkdir(parents=True, exist_ok=True)

        # Bug #3 fix: %(playlist_index)02d returns 'NA' on Bandcamp/SoundCloud.
        # Use autonumber which always gives a clean zero-padded integer.
        # Bug #4 fix: removed dead first outtmpl assignment (was immediately overridden).
        ydl_opts = _get_ydl_opts(download_type, fmt, bitrate,
                                  str(album_folder / '%(autonumber)03d_%(title)s.%(ext)s'))

        loop = asyncio.get_running_loop()
        completed = [0]

        def hook(d):
            if d['status'] == 'finished':
                completed[0] += 1
                pct = min(95, (completed[0] / max(track_count, 1)) * 95)
                if progress_callback and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        progress_callback(
                            pct,
                            f"Downloaded {completed[0]}/{track_count} tracks"
                        ),
                        loop
                    )

        ydl_opts['progress_hooks'] = [hook]

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

        await loop.run_in_executor(None, _download)

        # Remove any stray thumbnail/image files written by yt-dlp
        IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

        if download_type == 'cover_audio':
            # Bug #5 fix: For cover_audio non-concat, merge each downloaded audio file
            # with the album cover into individual video files, then clean up.
            cs = cover_settings or {}
            out_video_ext = cs.get('output_format', 'mp4')
            cover_ratio = cs.get('ratio', '1:1')
            cover_res    = cs.get('resolution', 'medium')

            # Find all audio files downloaded into the album folder
            AUDIO_EXTS = {'.mp3', '.m4a', '.aac', '.flac', '.ogg'}
            audio_files = sorted(
                f for f in album_folder.iterdir()
                if f.is_file() and f.suffix.lower() in AUDIO_EXTS
            )

            # Get album cover — try first image found, then fall back to metadata URL
            cover_path = None
            for img_file in album_folder.iterdir():
                if img_file.suffix.lower() in IMAGE_EXTS and img_file.stat().st_size > 0:
                    cover_path = str(img_file)
                    break
            if not cover_path and metadata.get('thumbnail'):
                downloaded_cover = str(album_folder / '_album_cover.jpg')
                result = await download_cover_image(metadata['thumbnail'], downloaded_cover)
                if result and os.path.exists(result):
                    cover_path = result

            if cover_path:
                for audio_file in audio_files:
                    video_out = audio_file.with_suffix(f'.{out_video_ext}')
                    try:
                        ok = await create_cover_audio_video(
                            audio_files=[str(audio_file)],
                            cover_path=cover_path,
                            output_path=str(video_out),
                            tracks_meta=[{'title': audio_file.stem, 'artist': artist, 'duration': 0}],
                            cover_ratio=cover_ratio,
                            cover_resolution=cover_res,
                            add_chapters=False,
                        )
                        if ok:
                            audio_file.unlink()  # remove source mp3
                    except Exception as e:
                        app_logger.error(f"cover_audio merge failed for {audio_file.name}: {e}")

            # Remove the cover image from the folder (not part of output)
            for img_file in album_folder.iterdir():
                if img_file.suffix.lower() in IMAGE_EXTS:
                    try:
                        img_file.unlink()
                    except Exception:
                        pass
        else:
            # For non-cover_audio: remove any stray image files
            for img_file in album_folder.iterdir():
                if img_file.suffix.lower() in IMAGE_EXTS:
                    try:
                        img_file.unlink()
                        app_logger.debug(f"Removed stray thumbnail: {img_file.name}")
                    except Exception:
                        pass

        # Count audio/video files only (not images, not temp files)
        MEDIA_EXTS = {'.mp3', '.flac', '.m4a', '.aac', '.mp4', '.mkv', '.webm', '.ogg'}
        files = [
            f for f in album_folder.iterdir()
            if f.is_file() and f.suffix.lower() in MEDIA_EXTS
        ]

        # Guard: if yt-dlp produced no media files (all tracks failed), raise clearly
        if not files:
            import shutil
            shutil.rmtree(str(album_folder), ignore_errors=True)
            raise RuntimeError(
                f"No media files were downloaded into {album_folder}. "
                "All tracks may have failed. Check yt-dlp logs for details."
            )

        total_size = sum(f.stat().st_size for f in files)

        return {
            'file_path': str(album_folder),
            'file_size': total_size,
            'track_count': len(files),
            'artist': artist,
            'album': album,
        }


def _find_downloaded_file(template_base: str, ext: str) -> Optional[str]:
    """
    Find a downloaded file matching the base template with the given extension.
    Handles extension aliases (e.g. aac -> .m4a) and fuzzy stem matching.
    """
    # Extension aliases: yt-dlp may produce a different extension than requested
    EXT_ALIASES = {
        'aac': ['aac', 'm4a'],
        'm4a': ['m4a', 'aac'],
        'mp3': ['mp3'],
        'flac': ['flac'],
        'mp4': ['mp4'],
        'mkv': ['mkv'],
        'webm': ['webm'],
        'jpg': ['jpg', 'jpeg', 'webp'],
        'png': ['png', 'webp'],
    }
    extensions_to_try = EXT_ALIASES.get(ext, [ext])

    parent = Path(template_base).parent
    stem = Path(template_base).name

    # 1. Direct match for each possible extension
    for try_ext in extensions_to_try:
        direct = Path(f"{template_base}.{try_ext}")
        if direct.exists():
            return str(direct)

    # 2. Fuzzy: scan directory for files starting with stem
    if parent.exists():
        for f in sorted(parent.iterdir()):  # sorted for determinism
            if f.name.startswith(stem) and f.suffix.lower().lstrip('.') in extensions_to_try:
                return str(f)

    return None
