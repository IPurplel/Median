import asyncio
import uuid
import json
from datetime import datetime
from typing import Dict, Optional, Any
from backend.db_models import get_db, row_to_dict
from backend.downloader import (
    download_single, download_playlist,
    cleanup_partials, discard_temp_entries,
)
from backend.metadata_handler import extract_metadata
from backend.logger import app_logger
from backend.config import settings

UPDATABLE_COLUMNS = frozenset({
    'status', 'progress', 'speed', 'eta', 'file_path',
    'file_size', 'error_message', 'warnings', 'lyrics',
})

# Failures a second attempt can never fix — retrying them just re-downloads
# the audio to hit the same wall. Anything not listed here is treated as
# transient (network, expired URL, HTTP 416 from a stale range) and retried.
PERMANENT_ERROR_MARKERS = (
    'cover image not found',
    'video unavailable',
    'private video',
    'members-only',
    'sign in to confirm your age',
    'removed by the uploader',
    'account associated with this video has been terminated',
    'unsupported url',
    'requested format is not available',
    'no video formats found',
)


def _is_permanent_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(marker in msg for marker in PERMANENT_ERROR_MARKERS)


active_downloads: Dict[str, asyncio.Task] = {}
download_states: Dict[str, dict] = {}
_cleanup_tasks: set = set()
_dl_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _dl_semaphore
    if _dl_semaphore is None:
        _dl_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_DOWNLOADS)
    return _dl_semaphore


def create_download_record(
    url: str,
    platform: str,
    download_type: str,
    fmt: str,
    bitrate: str,
    metadata: dict,
    concatenate: bool = False,
    cover_settings: Optional[dict] = None,
    source: str = 'manual',
    include_description: bool = False,
) -> str:
    download_id = str(uuid.uuid4())
    db = get_db()
    try:
        db.execute("""
            INSERT INTO downloads
            (id, url, platform, title, artist, album, duration, thumbnail_url,
             download_type, format, bitrate, status, progress, is_playlist,
             playlist_count, is_concatenated, cover_settings, source, tags,
             release_date, artist_url, include_description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            download_id, url, platform,
            metadata.get('title', ''), metadata.get('artist', ''),
            metadata.get('album', ''), metadata.get('duration') or metadata.get('total_duration', 0),
            metadata.get('thumbnail', ''),
            download_type, fmt, bitrate,
            1 if metadata.get('is_playlist') else 0,
            metadata.get('track_count', 0),
            1 if concatenate else 0,
            json.dumps(cover_settings) if cover_settings else None,
            source,
            json.dumps(metadata.get('tags') or []),
            metadata.get('release_date', '') or '',
            metadata.get('artist_url', '') or '',
            1 if include_description else 0,
        ))
        db.commit()
    finally:
        db.close()

    return download_id


def update_download_status(
    download_id: str,
    status: str,
    progress: float = None,
    speed: str = None,
    eta: str = None,
    file_path: str = None,
    file_size: int = None,
    error_message: str = None,
    warnings: list = None,
    lyrics: list = None,
):
    updates = {}
    updates['status'] = status
    if progress is not None:
        updates['progress'] = progress
    if speed is not None:
        updates['speed'] = speed
    if eta is not None:
        updates['eta'] = eta
    if file_path is not None:
        updates['file_path'] = file_path
    if file_size is not None:
        updates['file_size'] = file_size
    if error_message is not None:
        updates['error_message'] = error_message
    if warnings:
        updates['warnings'] = json.dumps(warnings)
    if lyrics:
        updates['lyrics'] = json.dumps(lyrics)
    if status == 'completed':
        updates["completed_at"] = "datetime('now')"

    for col in updates:
        if col != "completed_at" and col not in UPDATABLE_COLUMNS and col != 'status':
            app_logger.warning(f"Skipping non-updatable column: {col}")

    fields = []
    values = []
    for col, val in updates.items():
        if col == "completed_at":
            fields.append(f"{col} = datetime('now')")
        else:
            fields.append(f"{col} = ?")
            values.append(val)

    values.append(download_id)
    db = get_db()
    try:
        db.execute(
            f"UPDATE downloads SET {', '.join(fields)} WHERE id = ?",
            values
        )
        db.commit()
    finally:
        db.close()

    if download_id in download_states:
        download_states[download_id].update({
            'status': status,
            'progress': progress if progress is not None else download_states[download_id].get('progress', 0),
            'speed': speed or download_states[download_id].get('speed', ''),
            'eta': eta or download_states[download_id].get('eta', ''),
        })


async def process_download(download_id: str, download_params: dict):
    async with _get_semaphore():
        url = download_params['url']
        download_type = download_params['download_type']
        fmt = download_params['format']
        bitrate = download_params.get('bitrate', '')
        metadata = download_params['metadata']
        concatenate = download_params.get('concatenate', False)
        cover_settings = download_params.get('cover_settings')
        cover_id = download_params.get('cover_id')
        crossfade = download_params.get('crossfade', False)
        crossfade_duration = download_params.get('crossfade_duration')

        download_states[download_id] = {
            'id': download_id,
            'status': 'downloading',
            'progress': 0,
            'speed': '',
            'eta': '',
            'message': '',
            'total_tracks': metadata.get('track_count', 0),
            'is_playlist': bool(metadata.get('is_playlist')),
            'is_concatenated': bool(concatenate),
            'source': download_params.get('source', 'manual'),
            'title': metadata.get('title', ''),
            'artist': metadata.get('artist', ''),
            'warnings': [],
        }

        async def progress_callback(pct: float, message: str = '', warning: str = None):
            if warning:
                download_states[download_id].setdefault('warnings', []).append(warning)
            if pct is None:
                return
            download_states[download_id]['progress'] = pct
            download_states[download_id]['message'] = message
            last = download_states[download_id].get('_last_db_pct', -1)
            if pct - last >= 5:
                download_states[download_id]['_last_db_pct'] = pct
                # Skip DB write if task was already cancelled/cleaned externally
                db_chk = get_db()
                try:
                    row = db_chk.execute(
                        "SELECT status FROM downloads WHERE id = ?", (download_id,)
                    ).fetchone()
                    if row and row['status'] in ('cancelled', 'cleaned', 'error'):
                        return
                finally:
                    db_chk.close()
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, update_download_status, download_id, 'downloading', pct
                )
            app_logger.debug(f"[{download_id[:8]}] {pct:.0f}% - {message}")

        try:
            update_download_status(download_id, 'downloading', progress=0)

            is_playlist = metadata.get('is_playlist', False)

            async def _attempt():
                if is_playlist:
                    return await download_playlist(
                        url, download_type, fmt, bitrate, metadata,
                        concatenate=concatenate,
                        progress_callback=progress_callback,
                        cover_settings=cover_settings,
                        cover_id=cover_id,
                        crossfade=crossfade,
                        crossfade_duration=crossfade_duration,
                        download_id=download_id,
                    )
                return await download_single(
                    url, download_type, fmt, bitrate, metadata,
                    progress_callback=progress_callback,
                    cover_settings=cover_settings,
                    cover_id=cover_id,
                    download_id=download_id,
                )

            # One automatic fresh retry: transient failures (expired URLs,
            # HTTP 416 from stale ranges, dropped connections) usually succeed
            # on a clean second attempt. Partials are deleted between attempts
            # so nothing stale gets resumed.
            try:
                result = await _attempt()
            except asyncio.CancelledError:
                raise
            except Exception as first_err:
                cleanup_partials(download_id)
                if _is_permanent_error(first_err):
                    raise
                app_logger.warning(
                    f"Download attempt failed [{download_id[:8]}]: {first_err} — retrying once"
                )
                # Rewind progress bookkeeping, otherwise _last_db_pct from the
                # failed attempt suppresses DB writes until the retry passes it.
                download_states[download_id].update({
                    'progress': 0, 'speed': '', 'eta': '',
                    'message': 'Retrying after a failed attempt...',
                    '_last_db_pct': -1,
                })
                await asyncio.sleep(2)
                result = await _attempt()

            # Bandcamp publishes lyrics on each track page — fetch them for the
            # description.md. Non-fatal, and only after the download itself.
            lyrics = None
            if metadata.get('platform') == 'bandcamp':
                try:
                    from backend.utils.lyrics_fetcher import fetch_bandcamp_lyrics
                    loop = asyncio.get_running_loop()
                    lyrics = await loop.run_in_executor(
                        None, fetch_bandcamp_lyrics, metadata
                    )
                except Exception as e:
                    app_logger.warning(f"Lyrics fetch failed (non-fatal): {e}")

            # Embed the lyrics into the media tags too (USLT / ©lyr / LYRICS)
            # so music players display them per song.
            if lyrics and result.get('file_path'):
                try:
                    from backend.utils.tag_writer import embed_lyrics_into_download
                    tagged = await loop.run_in_executor(
                        None, embed_lyrics_into_download,
                        result['file_path'], lyrics, bool(concatenate),
                    )
                    if tagged:
                        app_logger.info(f"Lyrics embedded into {tagged} file(s)")
                except Exception as e:
                    app_logger.warning(f"Lyrics tag embed failed (non-fatal): {e}")

            update_download_status(
                download_id, 'completed',
                progress=100,
                file_path=result.get('file_path'),
                file_size=result.get('file_size', 0),
                warnings=download_states[download_id].get('warnings'),
                lyrics=lyrics,
            )

            actual_fmt = fmt
            if download_type == 'cover_audio':
                actual_fmt = (cover_settings or {}).get('output_format', 'mp4')

            _add_to_history(download_id, metadata, url, actual_fmt)

            download_states[download_id]['status'] = 'completed'
            download_states[download_id]['progress'] = 100
            app_logger.info(f"Download complete: {download_id[:8]} - {metadata.get('title')}")

        except asyncio.CancelledError:
            update_download_status(download_id, 'cancelled')
            download_states[download_id]['status'] = 'cancelled'
            app_logger.info(f"Download cancelled: {download_id[:8]}")
            # The yt-dlp executor thread may still be writing; delete what's
            # there now — the stale-partial sweep catches any late stragglers.
            cleanup_partials(download_id)
        except Exception as e:
            error_msg = str(e)
            cleanup_partials(download_id)
            update_download_status(download_id, 'error', error_message=error_msg)
            download_states[download_id]['status'] = 'error'
            download_states[download_id]['error'] = error_msg
            app_logger.error(f"Download error [{download_id[:8]}]: {error_msg}")
        finally:
            discard_temp_entries(download_id)
            if download_id in active_downloads:
                del active_downloads[download_id]
            t = asyncio.create_task(_deferred_state_cleanup(download_id))
            _cleanup_tasks.add(t)
            t.add_done_callback(_cleanup_tasks.discard)


async def _deferred_state_cleanup(download_id: str, delay: int = 300):
    await asyncio.sleep(delay)
    download_states.pop(download_id, None)


def _add_to_history(download_id: str, metadata: dict, url: str, fmt: str):
    db = get_db()
    try:
        row = db.execute(
            "SELECT file_size FROM downloads WHERE id = ?", (download_id,)
        ).fetchone()
        file_size = row['file_size'] if row else 0

        db.execute("""
            INSERT INTO history (download_id, url, title, artist, platform, format, file_size)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            download_id, url,
            metadata.get('title', ''), metadata.get('artist', ''),
            metadata.get('platform', ''), fmt, file_size
        ))
        db.commit()
    except Exception as e:
        app_logger.error(f"History add error: {e}")
    finally:
        db.close()


async def enqueue_download(download_params: dict) -> str:
    metadata = download_params['metadata']
    platform = metadata.get('platform', detect_platform_from_params(download_params))

    download_id = create_download_record(
        url=download_params['url'],
        platform=platform,
        download_type=download_params['download_type'],
        fmt=download_params['format'],
        bitrate=download_params.get('bitrate', ''),
        metadata=metadata,
        concatenate=download_params.get('concatenate', False),
        cover_settings=download_params.get('cover_settings'),
        source=download_params.get('source', 'manual'),
        include_description=download_params.get('include_description', False),
    )

    task = asyncio.create_task(process_download(download_id, download_params))
    active_downloads[download_id] = task

    app_logger.info(f"Enqueued: {download_id[:8]} - {metadata.get('title', download_params['url'])}")
    return download_id


def detect_platform_from_params(params: dict) -> str:
    from backend.utils.validators import detect_platform
    return detect_platform(params.get('url', '')) or 'unknown'


def cancel_download(download_id: str) -> bool:
    if download_id in active_downloads:
        active_downloads[download_id].cancel()
        return True
    return False


def _parse_warnings(raw) -> list:
    """DB `warnings` column (JSON array string) → list; tolerate legacy/empty."""
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def get_download_status(download_id: str) -> Optional[dict]:
    if download_id in download_states:
        return download_states[download_id]

    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM downloads WHERE id = ?", (download_id,)
        ).fetchone()
        d = row_to_dict(row)
        if d:
            # Map DB column names to the same keys the frontend expects from in-memory state
            d.setdefault('total_tracks', d.get('playlist_count', 0) or 0)
            d['is_playlist'] = bool(d.get('is_playlist'))
            d['is_concatenated'] = bool(d.get('is_concatenated'))
            d['warnings'] = _parse_warnings(d.get('warnings'))
        return d
    finally:
        db.close()


def get_queue() -> list:
    db = get_db()
    try:
        rows = db.execute(
            """SELECT * FROM downloads
               WHERE status IN ('queued', 'downloading')
               ORDER BY created_at ASC"""
        ).fetchall()
        result = [row_to_dict(r) for r in rows]

        for item in result:
            item['warnings'] = _parse_warnings(item.get('warnings'))
            if item['id'] in download_states:
                item.update(download_states[item['id']])

        return result
    finally:
        db.close()
