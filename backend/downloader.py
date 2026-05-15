import asyncio
import os
import uuid
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from backend.config import settings
from backend.utils.validators import detect_platform, is_playlist_url
from backend.utils.file_organizer import (
    get_single_track_filename, get_album_filename,
    get_playlist_folder, get_track_in_album_filename,
    ensure_unique_path, find_downloaded_file
)
from backend.concatenation_engine import (
    concatenate_audio, concatenate_video, create_cover_audio_video
)
from backend.image_processor import download_cover_image
from backend.logger import app_logger
from backend.utils.ydl_opts_builder import get_ydl_opts, FORMAT_EXT_MAP
from backend.utils.tag_writer import write_tags


def _album_meta(metadata: dict, title: str, artist: str, album: str = '') -> dict:
    """Bundle the curated tag fields Median wants embedded in every output."""
    return {
        'title': title,
        'artist': artist,
        'album': album or metadata.get('album', '') or '',
        'year': metadata.get('year', '') or '',
        'genre': metadata.get('genre', '') or '',
    }


CUSTOM_COVER_DIR = settings.custom_cover_path

async def download_single(
    url: str,
    download_type: str,
    fmt: str,
    bitrate: str,
    metadata: dict,
    progress_callback: Optional[Callable] = None,
    cover_settings: Optional[dict] = None,
    cover_id: Optional[str] = None,
) -> Dict[str, Any]:
    import yt_dlp

    download_dir = Path(settings.UPLOAD_FOLDER)
    download_dir.mkdir(parents=True, exist_ok=True)

    artist = metadata.get('artist') or 'Unknown Artist'
    title = metadata.get('title') or 'Unknown Title'
    if download_type == 'cover_audio':
        ext = (cover_settings or {}).get('output_format', 'mp4')
    else:
        ext = FORMAT_EXT_MAP.get(fmt, fmt)

    filename = get_single_track_filename(artist, title, ext)
    output_path = ensure_unique_path(download_dir / filename)
    temp_template = str(download_dir / f"_tmp_{uuid.uuid4().hex}")

    last_progress = {'pct': 0, 'speed': '', 'eta': ''}

    main_loop = asyncio.get_running_loop()

    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
            downloaded = d.get('downloaded_bytes', 0)
            pct = min(90, (downloaded / total) * 90)
            speed = d.get('_speed_str', '').strip()
            eta = d.get('_eta_str', '').strip()
            last_progress.update({'pct': pct, 'speed': speed, 'eta': eta})

            if progress_callback and main_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    progress_callback(pct, f"Downloading... {speed}"),
                    main_loop
                )

    ydl_opts = get_ydl_opts(download_type, fmt, bitrate, temp_template + '.%(ext)s', hook)

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    await asyncio.get_running_loop().run_in_executor(None, _download)

    audio_ext = 'mp3' if download_type == 'cover_audio' else ext

    downloaded_file = find_downloaded_file(temp_template, audio_ext)

    if not downloaded_file:
        # Clean up any partial temp files before raising
        parent = Path(temp_template).parent
        stem = Path(temp_template).name
        if parent.exists():
            for f in parent.iterdir():
                if f.name.startswith(stem):
                    try:
                        f.unlink()
                    except Exception:
                        pass
        raise FileNotFoundError(f"Download failed: no output file found for {url}")

    if download_type == 'cover_audio':
        cover_file = None
        parent_dir = Path(temp_template).parent
        stem = Path(temp_template).name

        if cover_id:
            matches = list(CUSTOM_COVER_DIR.glob(f"{cover_id}.*"))
            if not matches:
                raise RuntimeError(f"Uploaded cover image not found (id={cover_id}). Re-upload and try again.")
            cover_file = str(matches[0])
            app_logger.info(f"Using uploaded cover: {cover_file}")

        for img_ext in ('jpg', 'jpeg', 'png', 'webp'):
            if cover_file:
                break
            candidate = find_downloaded_file(temp_template, img_ext)
            if candidate and os.path.exists(candidate):
                cover_file = candidate
                app_logger.info(f"Cover found (direct): {candidate}")
                break

        if not cover_file and parent_dir.exists():
            for f in sorted(parent_dir.iterdir()):
                if (f.stem.startswith(stem) and
                        f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp') and
                        f.stat().st_size > 0):
                    cover_file = str(f)
                    app_logger.info(f"Cover found (scan): {cover_file}")
                    break

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
            if os.path.exists(downloaded_file):
                os.remove(downloaded_file)
            raise RuntimeError(
                "Cover image not found. yt-dlp may not have downloaded the thumbnail. "
                "Try a different URL or check if the platform supports thumbnail download."
            )

        cs = cover_settings or {}
        out_ext = cs.get('output_format', 'mp4')
        video_output = ensure_unique_path(
            download_dir / get_single_track_filename(artist, title, out_ext)
        )

        app_logger.info(
            f"Merging cover ({cover_file}) + audio ({downloaded_file}) -> {video_output}"
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
            album_meta=_album_meta(metadata, title, artist),
            cover_ratio=cs.get('ratio', '1:1'),
            cover_resolution=cs.get('resolution', 'original'),
            add_chapters=False,
        )

        # MP4 carries cover-as-video-stream, but AIMP/MusicBee/iTunes look at
        # the covr atom for album art. Embed it explicitly while cover_file
        # still exists on disk.
        if (ok and video_output.exists() and video_output.suffix.lower() == '.mp4'
                and cover_file and os.path.exists(cover_file)):
            write_tags(str(video_output),
                       **_album_meta(metadata, title, artist),
                       cover_path=cover_file)

        # Don't delete an uploaded cover — it may be re-used; only delete yt-dlp
        # temp files and URL-fetched fallback covers (which have the temp_template stem).
        cover_is_uploaded = cover_id and cover_file and str(CUSTOM_COVER_DIR) in cover_file
        for tmp_f in [downloaded_file] + ([] if cover_is_uploaded else [cover_file]):
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
        try:
            shutil.move(downloaded_file, str(output_path))
            final_path = str(output_path)
        except Exception:
            try:
                os.remove(downloaded_file)
            except OSError:
                pass
            raise

        # Authoritative tag rewrite. yt-dlp's FFmpegMetadata fills in much of
        # this already, but leaves album empty for YouTube and writes raw
        # YYYYMMDD as the date — fix it here using Median's curated metadata.
        am = _album_meta(metadata, title, artist)
        write_tags(final_path, **am)

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
    cover_settings: Optional[dict] = None,
    cover_id: Optional[str] = None,
) -> Dict[str, Any]:
    import yt_dlp

    download_dir = Path(settings.UPLOAD_FOLDER)
    artist = metadata.get('artist') or 'Unknown Artist'
    album  = metadata.get('album') or metadata.get('title') or 'Unknown Album'
    tracks = metadata.get('tracks', [])
    track_count = metadata.get('track_count', len(tracks))

    if download_type == 'cover_audio':
        ext = (cover_settings or {}).get('output_format', 'mp4')
    else:
        ext = FORMAT_EXT_MAP.get(fmt, fmt)

    if concatenate:
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
                    ydl_opts = get_ydl_opts(
                        'cover_audio', 'mp3', bitrate,
                        temp_template + '.%(ext)s'
                    )
                else:
                    ydl_opts = get_ydl_opts(
                        download_type, fmt, bitrate,
                        temp_template + '.%(ext)s'
                    )

                loop = asyncio.get_running_loop()

                def _dl(u=track_url, o=ydl_opts):
                    with yt_dlp.YoutubeDL(o) as ydl:
                        ydl.download([u])

                await loop.run_in_executor(None, _dl)

                dl_ext = 'mp3' if download_type == 'cover_audio' else ext
                f = find_downloaded_file(temp_template, dl_ext)
                if f:
                    downloaded_files.append(f)
                else:
                    track_title = track.get('title', f'track {i+1}')
                    app_logger.warning(f"Track {i+1} ({track_title!r}) failed to download — skipping")
                    if not track.get('duration'):
                        from backend.utils.ffmpeg_handler import get_media_duration
                        dur = get_media_duration(f)
                        tracks[i]['duration'] = dur or 0

                if download_type == 'cover_audio':
                    for img_ext in ('jpg', 'png', 'webp'):
                        cf = find_downloaded_file(temp_template, img_ext)
                        if cf:
                            downloaded_covers.append(cf)
                            break

            if not downloaded_files:
                raise ValueError(
                    "No tracks were downloaded successfully. "
                    "All playlist tracks may have failed — check the logs for details."
                )

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
                cover_file = None
                if cover_id:
                    matches = list(CUSTOM_COVER_DIR.glob(f"{cover_id}.*"))
                    if matches:
                        cover_file = str(matches[0])
                if not cover_file:
                    cover_file = downloaded_covers[0] if downloaded_covers else None
                if not cover_file:
                    cover_url = metadata.get('thumbnail', '')
                    if cover_url:
                        fallback_path = str(temp_dir / 'album_cover.jpg')
                        result_path = await download_cover_image(cover_url, fallback_path)
                        if result_path and os.path.exists(result_path):
                            cover_file = result_path
                        else:
                            cover_file = None

                ok = await create_cover_audio_video(
                    downloaded_files,
                    cover_file or '',
                    str(output_path),
                    tracks,
                    album_meta=_album_meta(metadata, album, artist, album=album),
                    cover_ratio=cover_settings.get('ratio', '1:1') if cover_settings else '1:1',
                    cover_resolution=cover_settings.get('resolution', 'original') if cover_settings else 'original',
                    add_chapters=True,
                    progress_callback=progress_callback
                )
            else:
                ok = False

            if not ok:
                raise RuntimeError("Concatenation failed")

            # For audio mode the concat just copies streams, so per-track tags
            # come along — but album/year/genre at the file level are missing.
            # Stamp them on the merged file (cover_audio gets these via -metadata
            # in the ffmpeg merge above).
            if download_type == 'audio' and output_path.exists():
                write_tags(str(output_path), **_album_meta(metadata, album, artist, album=album))
            # Cover+audio MP4: embed covr atom so AIMP shows album art.
            if (download_type == 'cover_audio' and output_path.suffix.lower() == '.mp4'
                    and output_path.exists() and cover_file and os.path.exists(cover_file)):
                write_tags(str(output_path),
                           **_album_meta(metadata, album, artist, album=album),
                           cover_path=cover_file)

            file_size = os.path.getsize(str(output_path)) if output_path.exists() else 0

            return {
                'file_path': str(output_path),
                'file_size': file_size,
                'track_count': len(downloaded_files),
                'artist': artist,
                'album': album,
            }

        finally:
            import shutil
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    else:
        album_folder = download_dir / get_playlist_folder(artist, album)
        album_folder.mkdir(parents=True, exist_ok=True)

        ydl_opts = get_ydl_opts(download_type, fmt, bitrate,
                                  str(album_folder / '%(autonumber)03d - %(title)s.%(ext)s'))

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

        IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}

        if download_type == 'cover_audio':
            cs = cover_settings or {}
            out_video_ext = cs.get('output_format', 'mp4')
            cover_ratio = cs.get('ratio', '1:1')
            cover_res    = cs.get('resolution', 'original')

            AUDIO_EXTS = {'.mp3', '.m4a', '.aac', '.flac', '.ogg'}
            audio_files = sorted(
                f for f in album_folder.iterdir()
                if f.is_file() and f.suffix.lower() in AUDIO_EXTS
            )

            cover_path = None
            if cover_id:
                matches = list(CUSTOM_COVER_DIR.glob(f"{cover_id}.*"))
                if matches:
                    cover_path = str(matches[0])
            if not cover_path:
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
                for i, audio_file in enumerate(audio_files):
                    video_out = audio_file.with_suffix(f'.{out_video_ext}')
                    # Prefer the already-cleaned track title from extracted
                    # metadata. Fall back to the filename only if we ran out
                    # of metadata entries — parsing the filename here would
                    # re-introduce the "Artist - Song" prefix yt-dlp's
                    # %(title)s template wrote in.
                    track_title = ''
                    if i < len(tracks):
                        track_title = (tracks[i].get('title') or '').strip()
                    if not track_title:
                        raw_stem = audio_file.stem
                        track_title = (
                            raw_stem.split(' - ', 1)[1]
                            if ' - ' in raw_stem else raw_stem
                        )
                    try:
                        ok = await create_cover_audio_video(
                            audio_files=[str(audio_file)],
                            cover_path=cover_path,
                            output_path=str(video_out),
                            tracks_meta=[{'title': track_title, 'artist': artist, 'duration': 0}],
                            album_meta=_album_meta(metadata, track_title, artist, album=album),
                            cover_ratio=cover_ratio,
                            cover_resolution=cover_res,
                            add_chapters=False,
                        )
                        if ok:
                            # Embed covr atom for MP4 outputs so AIMP shows album art
                            if video_out.suffix.lower() == '.mp4' and video_out.exists():
                                write_tags(str(video_out),
                                           **_album_meta(metadata, track_title, artist, album=album),
                                           cover_path=cover_path)
                            audio_file.unlink()
                    except Exception as e:
                        app_logger.error(f"cover_audio merge failed for {audio_file.name}: {e}")

            for img_file in album_folder.iterdir():
                if img_file.suffix.lower() in IMAGE_EXTS:
                    try:
                        img_file.unlink()
                    except Exception:
                        pass
        else:
            for img_file in album_folder.iterdir():
                if img_file.suffix.lower() in IMAGE_EXTS:
                    try:
                        img_file.unlink()
                        app_logger.debug(f"Removed stray thumbnail: {img_file.name}")
                    except Exception:
                        pass

            # For audio mode, yt-dlp tagged each track individually but
            # left album empty. Stamp album/year/genre on every track using
            # the playlist-level metadata.
            if download_type == 'audio':
                AUDIO_TAG_EXTS = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus'}
                for audio_file in album_folder.iterdir():
                    if audio_file.is_file() and audio_file.suffix.lower() in AUDIO_TAG_EXTS:
                        # Leave title and artist alone — yt-dlp populated them
                        # per-track. We just fill in the missing album-level fields.
                        write_tags(
                            str(audio_file),
                            album=album,
                            year=metadata.get('year', '') or '',
                            genre=metadata.get('genre', '') or '',
                        )

        MEDIA_EXTS = {'.mp3', '.flac', '.m4a', '.aac', '.mp4', '.mkv', '.webm', '.ogg'}
        files = [
            f for f in album_folder.iterdir()
            if f.is_file() and f.suffix.lower() in MEDIA_EXTS
        ]

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
