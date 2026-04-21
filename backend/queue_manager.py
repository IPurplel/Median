import asyncio
import uuid
import json
from datetime import datetime
from typing import Dict, Optional, Any
from backend.db_models import get_db, row_to_dict
from backend.downloader import download_single, download_playlist
from backend.metadata_handler import extract_metadata
from backend.logger import app_logger

# Global state
active_downloads: Dict[str, asyncio.Task] = {}
download_states: Dict[str, dict] = {}


def create_download_record(
    url: str,
    platform: str,
    download_type: str,
    fmt: str,
    bitrate: str,
    metadata: dict,
    concatenate: bool = False,
    cover_settings: Optional[dict] = None
) -> str:
    """Create download record in DB, return ID."""
    download_id = str(uuid.uuid4())
    db = get_db()
    try:
        db.execute("""
            INSERT INTO downloads
            (id, url, platform, title, artist, album, duration, thumbnail_url,
             download_type, format, bitrate, status, progress, is_playlist,
             playlist_count, is_concatenated, cover_settings, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, datetime('now'))
        """, (
            download_id, url, platform,
            metadata.get('title', ''), metadata.get('artist', ''),
            metadata.get('album', ''), metadata.get('duration') or metadata.get('total_duration', 0),
            metadata.get('thumbnail', ''),
            download_type, fmt, bitrate,
            1 if metadata.get('is_playlist') else 0,
            metadata.get('track_count', 0),
            1 if concatenate else 0,
            json.dumps(cover_settings) if cover_settings else None
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
    error_message: str = None
):
    db = get_db()
    try:
        fields = ['status = ?']
        values = [status]

        if progress is not None:
            fields.append('progress = ?')
            values.append(progress)
        if speed is not None:
            fields.append('speed = ?')
            values.append(speed)
        if eta is not None:
            fields.append('eta = ?')
            values.append(eta)
        if file_path is not None:
            fields.append('file_path = ?')
            values.append(file_path)
        if file_size is not None:
            fields.append('file_size = ?')
            values.append(file_size)
        if error_message is not None:
            fields.append('error_message = ?')
            values.append(error_message)
        if status == 'completed':
            fields.append("completed_at = datetime('now')")

        values.append(download_id)
        db.execute(
            f"UPDATE downloads SET {', '.join(fields)} WHERE id = ?",
            values
        )
        db.commit()
    finally:
        db.close()

    # Update in-memory state
    if download_id in download_states:
        download_states[download_id].update({
            'status': status,
            'progress': progress if progress is not None else download_states[download_id].get('progress', 0),
            'speed': speed or download_states[download_id].get('speed', ''),
            'eta': eta or download_states[download_id].get('eta', ''),
        })


async def process_download(download_id: str, download_params: dict):
    """Main download processing coroutine."""
    url = download_params['url']
    download_type = download_params['download_type']
    fmt = download_params['format']
    bitrate = download_params.get('bitrate', '')
    metadata = download_params['metadata']
    concatenate = download_params.get('concatenate', False)
    cover_settings = download_params.get('cover_settings')

    download_states[download_id] = {
        'id': download_id,
        'status': 'downloading',
        'progress': 0,
        'speed': '',
        'eta': '',
        'title': metadata.get('title', ''),
        'artist': metadata.get('artist', ''),
    }

    async def progress_callback(pct: float, message: str = ''):
        update_download_status(download_id, 'downloading', progress=pct)
        download_states[download_id]['progress'] = pct
        download_states[download_id]['message'] = message
        app_logger.debug(f"[{download_id[:8]}] {pct:.0f}% - {message}")

    try:
        update_download_status(download_id, 'downloading', progress=0)

        is_playlist = metadata.get('is_playlist', False)

        if is_playlist:
            result = await download_playlist(
                url, download_type, fmt, bitrate, metadata,
                concatenate=concatenate,
                progress_callback=progress_callback,
                cover_settings=cover_settings
            )
        else:
            result = await download_single(
                url, download_type, fmt, bitrate, metadata,
                progress_callback=progress_callback,
                cover_settings=cover_settings
            )

        update_download_status(
            download_id, 'completed',
            progress=100,
            file_path=result.get('file_path'),
            file_size=result.get('file_size', 0)
        )

        # Add to history — use actual output format, not internal fmt.
        # For cover_audio: fmt='mp3' internally but output is mp4/mkv/webm.
        # cover_settings may be None if user relied on defaults (mp4).
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
    except Exception as e:
        error_msg = str(e)
        update_download_status(download_id, 'error', error_message=error_msg)
        download_states[download_id]['status'] = 'error'
        download_states[download_id]['error'] = error_msg
        app_logger.error(f"Download error [{download_id[:8]}]: {error_msg}")
    finally:
        if download_id in active_downloads:
            del active_downloads[download_id]
        # Bug #8 fix: schedule cleanup of in-memory state after 5 min so
        # the UI can read the final state, but memory doesn't leak indefinitely
        asyncio.create_task(_deferred_state_cleanup(download_id))


async def _deferred_state_cleanup(download_id: str, delay: int = 300):
    """Remove download state from memory after delay seconds (default 5 min)."""
    await asyncio.sleep(delay)
    download_states.pop(download_id, None)


def _add_to_history(download_id: str, metadata: dict, url: str, fmt: str):
    db = get_db()
    try:
        # Get file info
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
    """Add download to queue and start processing."""
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
        cover_settings=download_params.get('cover_settings')
    )

    # Start download as background task (unlimited concurrent)
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


def get_download_status(download_id: str) -> Optional[dict]:
    # Check in-memory first for live data
    if download_id in download_states:
        return download_states[download_id]

    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM downloads WHERE id = ?", (download_id,)
        ).fetchone()
        return row_to_dict(row)
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

        # Enrich with live state
        for item in result:
            if item['id'] in download_states:
                item.update(download_states[item['id']])

        return result
    finally:
        db.close()
