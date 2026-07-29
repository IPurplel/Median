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
    get_playlist_folder,
    ensure_unique_path, find_downloaded_file
)
from backend.concatenation_engine import (
    concatenate_audio, concatenate_video, create_cover_audio_video,
    attach_cover_to_mkv,
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

# Temp paths each in-flight download writes to, keyed by download id, so the
# queue can delete leftovers when a download errors or is cancelled.
# Entry kinds: 'stem' (unlink every file matching <path>*), 'dir' (rmtree the
# whole dir), 'partials' (only remove .part/.ytdl inside — the dir may hold
# finished tracks worth keeping).
_temp_registry: Dict[str, list] = {}

PARTIAL_SUFFIXES = ('.part', '.ytdl')


def _register_temp(download_id: Optional[str], kind: str, path) -> None:
    if download_id:
        _temp_registry.setdefault(download_id, []).append((kind, str(path)))


def discard_temp_entries(download_id: str) -> None:
    """Forget a download's temp paths without touching the filesystem."""
    _temp_registry.pop(download_id, None)


def cleanup_partials(download_id: str) -> int:
    """Delete the partial files a failed/cancelled download left behind.

    Returns the number of filesystem entries removed. Safe to call for ids
    that never registered anything.
    """
    removed = 0
    for kind, raw in _temp_registry.pop(download_id, []):
        path = Path(raw)
        try:
            if kind == 'dir':
                if path.is_dir():
                    shutil.rmtree(str(path), ignore_errors=True)
                    removed += 1
            elif kind == 'stem':
                for f in path.parent.glob(path.name + '*'):
                    try:
                        f.unlink()
                        removed += 1
                    except OSError:
                        pass
            elif kind == 'partials':
                if path.is_dir():
                    for f in path.rglob('*'):
                        if f.is_file() and f.suffix.lower() in PARTIAL_SUFFIXES:
                            try:
                                f.unlink()
                                removed += 1
                            except OSError:
                                pass
        except OSError as e:
            app_logger.warning(f"Partial cleanup error for {raw}: {e}")
    if removed:
        app_logger.info(
            f"Removed {removed} leftover partial file(s) for {download_id[:8]}"
        )
    return removed


async def _resolve_cover_file(
    cover_id: Optional[str],
    local_candidates: list,
    thumbnail_url: str,
    fallback_path: str,
    require_uploaded: bool = False,
) -> Optional[str]:
    """Resolve the cover image for a cover+audio merge.

    Priority: user-uploaded cover (by cover_id) → local candidate files
    (yt-dlp-downloaded thumbnails) → download from the metadata thumbnail URL.
    """
    if cover_id:
        matches = list(CUSTOM_COVER_DIR.glob(f"{cover_id}.*"))
        if matches:
            app_logger.info(f"Using uploaded cover: {matches[0]}")
            return str(matches[0])
        if require_uploaded:
            raise RuntimeError(
                f"Uploaded cover image not found (id={cover_id}). Re-upload and try again."
            )

    for candidate in local_candidates:
        if candidate and os.path.exists(candidate) and os.path.getsize(candidate) > 0:
            app_logger.info(f"Cover found: {candidate}")
            return str(candidate)

    if thumbnail_url:
        app_logger.info("Downloading cover from metadata thumbnail URL...")
        result = await download_cover_image(thumbnail_url, fallback_path)
        if result and os.path.exists(result):
            app_logger.info(f"Cover downloaded from URL: {result}")
            return result

    return None


_WEBM_COVER_NOTE = (
    "WebM files can't hold embedded album art — the cover is saved separately. "
    "Choose MKV or MP4 if you want it embedded."
)


async def _warn(progress_callback: Optional[Callable], message: str):
    """Send a non-fatal, user-visible note through the queue's progress channel."""
    app_logger.warning(message)
    if not progress_callback:
        return
    try:
        await progress_callback(None, '', warning=message)
    except TypeError:
        pass


def _embed_mp4_cover(output_path: Path, cover_file: Optional[str], album_meta: dict):
    """MP4 carries cover-as-video-stream, but AIMP/MusicBee/iTunes look at the
    covr atom for album art — embed it explicitly while the cover still exists."""
    if (output_path.suffix.lower() == '.mp4' and output_path.exists()
            and cover_file and os.path.exists(cover_file)):
        write_tags(str(output_path), **album_meta, cover_path=cover_file)

async def download_single(
    url: str,
    download_type: str,
    fmt: str,
    bitrate: str,
    metadata: dict,
    progress_callback: Optional[Callable] = None,
    cover_settings: Optional[dict] = None,
    cover_id: Optional[str] = None,
    download_id: Optional[str] = None,
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
    _register_temp(download_id, 'stem', temp_template)

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
        parent_dir = Path(temp_template).parent
        stem = Path(temp_template).name

        local_candidates = [
            find_downloaded_file(temp_template, img_ext)
            for img_ext in ('jpg', 'jpeg', 'png', 'webp')
        ]
        if parent_dir.exists():
            local_candidates += [
                str(f) for f in sorted(parent_dir.iterdir())
                if f.stem.startswith(stem)
                and f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp')
            ]

        cover_file = await _resolve_cover_file(
            cover_id, local_candidates, metadata.get('thumbnail', ''),
            temp_template + '_cover.jpg', require_uploaded=True,
        )

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

        if ok:
            _embed_mp4_cover(video_output, cover_file, _album_meta(metadata, title, artist))

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
            if out_ext == 'webm':
                await _warn(progress_callback, _WEBM_COVER_NOTE)
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

        # yt-dlp keeps the written thumbnail file next to the media after
        # embedding it — sweep any remaining temp-stem side files.
        for side_file in Path(temp_template).parent.glob(Path(temp_template).name + '*'):
            try:
                side_file.unlink()
            except Exception:
                pass

        # Authoritative tag rewrite. yt-dlp's FFmpegMetadata fills in much of
        # this already, but leaves album empty for YouTube and writes raw
        # YYYYMMDD as the date — fix it here using Median's curated metadata.
        am = _album_meta(metadata, title, artist)
        write_tags(final_path, **am)

        if download_type == 'video' and ext == 'webm':
            await _warn(progress_callback, _WEBM_COVER_NOTE)

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
    crossfade: bool = False,
    crossfade_duration: Optional[float] = None,
    download_id: Optional[str] = None,
    selected_indices: Optional[list] = None,
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
        _register_temp(download_id, 'dir', temp_dir)

        downloaded_files = []
        downloaded_tracks = []   # tracks_meta kept 1:1 with downloaded_files —
                                 # a skipped track must not shift chapters/durations
                                 # onto the wrong songs
        downloaded_covers = []

        try:
            for i, track in enumerate(tracks):
                track_url = track.get('url', '')
                if not track_url:
                    await _warn(
                        progress_callback,
                        f"Track {i+1} ({track.get('title', '?')}) has no URL — skipped"
                    )
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

                try:
                    await loop.run_in_executor(None, _dl)
                except Exception as e:
                    app_logger.warning(f"Track {i+1} download raised: {e}")

                dl_ext = 'mp3' if download_type == 'cover_audio' else ext
                f = find_downloaded_file(temp_template, dl_ext)
                if f:
                    downloaded_files.append(f)
                    downloaded_tracks.append(track)
                    if not track.get('duration'):
                        from backend.utils.ffmpeg_handler import get_media_duration
                        dur = get_media_duration(f)
                        tracks[i]['duration'] = dur or 0
                else:
                    track_title = track.get('title', f'track {i+1}')
                    await _warn(
                        progress_callback,
                        f"Track {i+1} ({track_title}) failed to download — skipped in the merge"
                    )

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
                    downloaded_files, str(output_path), downloaded_tracks,
                    add_chapters=True, progress_callback=progress_callback,
                    crossfade=crossfade, crossfade_duration=crossfade_duration,
                    bitrate=bitrate,
                )
            elif download_type == 'video':
                ok = await concatenate_video(
                    downloaded_files, str(output_path), fmt,
                    progress_callback=progress_callback,
                    crossfade=crossfade, crossfade_duration=crossfade_duration,
                )
            elif download_type == 'cover_audio':
                cover_file = await _resolve_cover_file(
                    cover_id, downloaded_covers, metadata.get('thumbnail', ''),
                    str(temp_dir / 'album_cover.jpg'),
                )

                ok = await create_cover_audio_video(
                    downloaded_files,
                    cover_file or '',
                    str(output_path),
                    downloaded_tracks,
                    album_meta=_album_meta(metadata, album, artist, album=album),
                    cover_ratio=cover_settings.get('ratio', '1:1') if cover_settings else '1:1',
                    cover_resolution=cover_settings.get('resolution', 'original') if cover_settings else 'original',
                    add_chapters=True,
                    progress_callback=progress_callback,
                    crossfade=crossfade,
                    crossfade_duration=crossfade_duration,
                )
            else:
                ok = False

            if not ok:
                raise RuntimeError("Concatenation failed")

            # Merging drops per-track cover art (the crossfade/concat re-encode
            # doesn't carry it), and audio concat leaves album/year/genre empty.
            # Stamp the curated tags plus the album cover onto the merged file.
            # MKV takes the cover as an attachment, WebM can't embed art at all.
            am = _album_meta(metadata, album, artist, album=album)
            out_ext = output_path.suffix.lower()
            if download_type in ('audio', 'video') and output_path.exists():
                merged_cover = None
                if out_ext == '.webm':
                    await _warn(progress_callback, _WEBM_COVER_NOTE)
                else:
                    merged_cover = await _resolve_cover_file(
                        None, downloaded_covers, metadata.get('thumbnail', ''),
                        str(temp_dir / 'album_cover.jpg'),
                    )
                if out_ext == '.mkv':
                    if merged_cover:
                        loop = asyncio.get_running_loop()
                        if not await loop.run_in_executor(
                            None, attach_cover_to_mkv, str(output_path), merged_cover
                        ):
                            app_logger.warning("Could not attach cover to merged MKV")
                else:
                    # mp3/flac/m4a/mp4 all embed via mutagen; unsupported
                    # extensions are skipped inside write_tags.
                    write_tags(str(output_path), **am, cover_path=merged_cover)
            if download_type == 'cover_audio':
                _embed_mp4_cover(output_path, cover_file, am)
                if out_ext == '.webm':
                    await _warn(progress_callback, _WEBM_COVER_NOTE)

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
        _register_temp(download_id, 'partials', album_folder)

        # With a partial selection, keep each track's original album position in
        # the filename so picked tracks still sort alongside the rest of the
        # album (autonumber would renumber them 1..N). Falls back to autonumber
        # on platforms that don't report playlist_index.
        number_field = (
            '%(playlist_index,autonumber)03d' if selected_indices else '%(autonumber)03d'
        )
        ydl_opts = get_ydl_opts(download_type, fmt, bitrate,
                                  str(album_folder / f'{number_field} - %(title)s.%(ext)s'))
        if selected_indices:
            # Tell yt-dlp to fetch only the ticked tracks rather than the album.
            ydl_opts['playlist_items'] = ','.join(str(i) for i in selected_indices)

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

            cover_path = await _resolve_cover_file(
                cover_id,
                [str(f) for f in album_folder.iterdir() if f.suffix.lower() in IMAGE_EXTS],
                metadata.get('thumbnail', ''),
                str(album_folder / '_album_cover.jpg'),
            )

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
                            _embed_mp4_cover(video_out, cover_path,
                                             _album_meta(metadata, track_title, artist, album=album))
                            audio_file.unlink()
                    except Exception as e:
                        app_logger.error(f"cover_audio merge failed for {audio_file.name}: {e}")

            # AIMP (and most music players) fall back to a sidecar cover.jpg /
            # cover.png in the same folder when a file's own tags don't carry
            # cover art — and MKV cover attachments specifically don't surface
            # in AIMP. Keep the cover as cover.<ext> in the album folder so
            # MKV cover+audio outputs still get album art in player libraries.
            sidecar_kept: Optional[Path] = None
            if cover_path and os.path.exists(cover_path):
                src = Path(cover_path)
                ext = src.suffix.lower()
                if ext not in ('.jpg', '.jpeg', '.png'):
                    ext = '.jpg'  # other formats are unreliable as sidecars
                target = album_folder / f'cover{ext}'
                try:
                    if src.resolve() != target.resolve():
                        import shutil as _sh
                        _sh.copyfile(src, target)
                    sidecar_kept = target
                except Exception as e:
                    app_logger.warning(f"sidecar cover.jpg copy failed: {e}")

            for img_file in album_folder.iterdir():
                if (img_file.suffix.lower() in IMAGE_EXTS
                        and (sidecar_kept is None or img_file != sidecar_kept)):
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

            # WebM tracks can't carry embedded art — leave a sidecar cover.jpg
            # in the album folder (players fall back to it) and tell the user.
            if download_type == 'video' and fmt == 'webm':
                sidecar = await _resolve_cover_file(
                    None, [], metadata.get('thumbnail', ''),
                    str(album_folder / 'cover.jpg'),
                )
                if sidecar:
                    app_logger.info(f"Sidecar cover saved for WebM album: {sidecar}")
                await _warn(progress_callback, _WEBM_COVER_NOTE)

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
