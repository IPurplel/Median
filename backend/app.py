import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import settings
from backend.db_models import init_db, get_db, row_to_dict
from backend.logger import app_logger
from backend.metadata_handler import extract_metadata
from backend.queue_manager import (
    enqueue_download, cancel_download, get_download_status, get_queue
)
from backend.backup_manager import create_backup, restore_backup, get_backup_list, delete_backup
from backend.watched_folder_monitor import (
    start_watching, stop_watching, get_watched_status, set_url_callback
)
from backend.scheduler import start_scheduler
from backend.utils.validators import validate_url
from backend.utils.file_organizer import format_file_size, format_duration
from backend.utils.ffmpeg_handler import is_ffmpeg_available

# ── Lifespan (replaces deprecated @app.on_event) ─────────────────────────────

_watch_task: Optional[asyncio.Task] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    global _watch_task
    app_logger.info("Initializing Median...")
    from backend.config import ensure_directories
    ensure_directories()  # Ensure dirs exist at startup (Docker or local)
    init_db()
    start_scheduler()

    async def on_watched_url(url: str, platform: str):
        meta = await extract_metadata(url)
        if 'error' not in meta:
            await enqueue_download({
                'url': url,
                'download_type': 'audio',
                'format': 'mp3',
                'bitrate': '320',
                'metadata': meta,
            })

    set_url_callback(on_watched_url)
    _watch_task = asyncio.create_task(start_watching())
    app_logger.info("Median ready ✓")

    yield  # ── App runs here ─────────────────────────────────────────────

    # ── Shutdown ─────────────────────────────────────────────────────────
    stop_watching()
    if _watch_task:
        _watch_task.cancel()
        try:
            await asyncio.wait_for(_watch_task, timeout=3)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    from backend.scheduler import scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)
    app_logger.info("Median shut down cleanly")


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Median", version="1.0.0", docs_url="/api/docs", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic models ───────────────────────────────────────────────────────────

class ValidateRequest(BaseModel):
    url: str

class CoverSettings(BaseModel):
    ratio: str = "1:1"
    resolution: str = "medium"
    output_format: str = "mp4"

class DownloadRequest(BaseModel):
    url: str
    download_type: str          # audio | video | cover_audio
    format: str                 # mp3 | flac | aac | mp4 | mkv | webm
    bitrate: Optional[str] = ""
    concatenate: bool = False
    cover_settings: Optional[CoverSettings] = None

class BackupRequest(BaseModel):
    selection: str = "all"
    date_from: Optional[str] = None
    date_to: Optional[str] = None

class KeepFileRequest(BaseModel):
    keep: bool

# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "ffmpeg": is_ffmpeg_available(),
        "timestamp": datetime.now().isoformat(),
    }

# ── Platform status ───────────────────────────────────────────────────────────

@app.get("/api/platforms")
async def platform_status():
    """Check connectivity to supported platforms — all 3 run concurrently."""
    import aiohttp

    platforms = {
        "youtube":    "https://www.youtube.com",
        "soundcloud": "https://soundcloud.com",
        "bandcamp":   "https://bandcamp.com",
    }

    async def check_one(name: str, url: str) -> tuple:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5),
                    allow_redirects=True
                ) as r:
                    return name, r.status < 400
        except Exception:
            return name, False

    # Run all 3 checks concurrently — total wait = max(5s, 5s, 5s) = 5s, not 15s
    results = await asyncio.gather(
        *[check_one(name, url) for name, url in platforms.items()]
    )
    return dict(results)

# ── Validate & Metadata ───────────────────────────────────────────────────────

@app.post("/api/validate")
async def validate(req: ValidateRequest):
    is_valid, platform, error = validate_url(req.url)
    if not is_valid:
        raise HTTPException(400, error)

    meta = await extract_metadata(req.url)
    if 'error' in meta:
        raise HTTPException(422, meta['error'])

    # Enrich display data
    meta['platform'] = platform
    if meta.get('duration'):
        meta['duration_display'] = format_duration(meta['duration'])
    if meta.get('total_duration'):
        meta['total_duration_display'] = format_duration(meta['total_duration'])

    return meta

# ── Download ──────────────────────────────────────────────────────────────────

@app.post("/api/download")
async def start_download(req: DownloadRequest):
    # Bug #9 fix: skip redundant validate_url() — the client already called
    # /api/validate. Just detect the platform for recording purposes.
    from backend.utils.validators import detect_platform as _detect
    platform = _detect(req.url) or 'unknown'

    # Bug #8 fix: extract_metadata is cached — the validate call already populated
    # the cache so this returns instantly on the second call (cache hit).
    meta = await extract_metadata(req.url)
    if 'error' in meta:
        raise HTTPException(422, meta['error'])

    meta['platform'] = platform

    cover_settings_dict = req.cover_settings.dict() if req.cover_settings else None

    download_id = await enqueue_download({
        'url': req.url,
        'download_type': req.download_type,
        'format': req.format,
        'bitrate': req.bitrate,
        'concatenate': req.concatenate,
        'metadata': meta,
        'cover_settings': cover_settings_dict,
    })

    return {
        'download_id': download_id,
        'status': 'queued',
        'title': meta.get('title', ''),
        'artist': meta.get('artist', ''),
    }

# ── Download Status ───────────────────────────────────────────────────────────

@app.get("/api/download/{download_id}/status")
async def download_status(download_id: str):
    status = get_download_status(download_id)
    if not status:
        raise HTTPException(404, "Download not found")
    return status


@app.delete("/api/download/{download_id}")
async def cancel(download_id: str):
    ok = cancel_download(download_id)
    return {"cancelled": ok}


@app.post("/api/download/{download_id}/keep")
async def set_keep(download_id: str, req: KeepFileRequest):
    db = get_db()
    try:
        db.execute(
            "UPDATE downloads SET keep_file = ? WHERE id = ?",
            (1 if req.keep else 0, download_id)
        )
        db.commit()
    finally:
        db.close()
    return {"keep": req.keep}

# ── Title-only filename extractor ────────────────────────────────────────────

def _title_only(raw: str) -> str:
    """
    Extract the song title from a raw filename stem, stripping common prefixes:
      - Leading track numbers:  "01 ", "01_", "01 - ", "01. ", "001 - " …
      - Artist - Title pattern: "Artist - Title"  →  "Title"
      - yt-dlp underscore-dash: "Artist_-_Title"  →  "Title"
      - Simple underscore join: "Artist_Title"    →  "Title"
    Returns a clean, non-empty string safe for use as a filename.
    """
    import re
    s = raw.strip()

    # 1. Strip leading numeric index  e.g. "01 - ", "002_", "1. ", "003 - "
    s = re.sub(r'^\d+[\s_\-\.]+', '', s).strip()

    # 2. Handle yt-dlp's underscore-dash style: "Artist_-_Title"
    if '_-_' in s:
        parts = s.split('_-_', 1)
        if parts[1].strip('_').strip():
            s = parts[1].strip('_').strip()

    # 3. Handle space-dash-space: "Artist - Title"
    elif ' - ' in s:
        parts = s.split(' - ', 1)
        if parts[1].strip():
            s = parts[1].strip()

    # 4. Pure underscore with no spaces: "Artist_SongTitle" → strip first segment
    elif '_' in s and ' ' not in s:
        candidate = re.sub(r'^[^_]+_', '', s, count=1).strip('_').strip()
        if len(candidate) >= 3:
            s = candidate

    # 5. Replace remaining underscores with spaces for readability
    s = s.replace('_', ' ').strip()

    # 6. Strip filesystem-unsafe characters
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', s).strip()

    return s if s else raw.replace('_', ' ').strip()


# ── File Download (always served as ZIP) ─────────────────────────────────────

@app.get("/api/download/{download_id}/file")
async def get_file(download_id: str):
    import zipfile, re, tempfile
    from backend.utils.validators import sanitize_filename

    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM downloads WHERE id = ?", (download_id,)
        ).fetchone()
    finally:
        db.close()

    if not row or not row['file_path']:
        raise HTTPException(404, "File not found")

    file_path = Path(row['file_path'])
    if not file_path.exists():
        raise HTTPException(410, "File has been cleaned up — please download again.")

    # ── Build ZIP name ────────────────────────────────────────────────────
    # Bug #10 fix: sanitize_filename can return '' for names with only special chars.
    # Add or-fallback after sanitization to guarantee a non-empty filename component.
    artist = sanitize_filename(row['artist'] or '') or 'Unknown'
    title  = sanitize_filename(row['title']  or '') or 'Download'
    album  = sanitize_filename(row['album']  or row['title'] or '') or 'Album'
    is_playlist = bool(row['is_playlist'])

    zip_name = f"{artist}_{album}.zip" if is_playlist else f"{artist}_{title}.zip"

    # ── Write ZIP to temp file (avoids buffering entire file in RAM) ──────
    tmp = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
    tmp.close()

    def build_zip():
        try:
            with zipfile.ZipFile(tmp.name, 'w', zipfile.ZIP_STORED) as zf:
                if file_path.is_file():
                    inner_ext = file_path.suffix
                    if is_playlist:
                        inner = f"{_title_only(album)}{inner_ext}"
                    else:
                        inner = f"{_title_only(title)}{inner_ext}"
                    zf.write(str(file_path), inner)

                elif file_path.is_dir():
                    files = sorted(
                        f for f in file_path.iterdir()
                        if f.is_file() and not f.name.startswith('.')
                    )
                    for f in files:
                        inner = f"{_title_only(f.stem)}{f.suffix}"
                        zf.write(str(f), inner)
        except FileNotFoundError:
            # File or folder was cleaned up between the exists() check and here
            raise  # Re-raise so the executor surfaces it as a 410

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, build_zip)
    except FileNotFoundError:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        raise HTTPException(410, "File has been cleaned up — please download again.")

    zip_size = os.path.getsize(tmp.name)

    async def stream_zip():
        try:
            with open(tmp.name, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass

    # RFC 5987 encoding so non-ASCII characters (e.g. em-dashes from Bandcamp
    # album titles like "— — —") don't cause a latin-1 encode error.
    # We send both forms for maximum client compatibility:
    #   filename=      → ASCII-safe fallback (non-ASCII bytes replaced with '?')
    #   filename*=     → RFC 5987 UTF-8 percent-encoded (used by all modern browsers)
    from urllib.parse import quote as _url_quote
    zip_name_ascii    = zip_name.encode('ascii', 'replace').decode()   # '?' for non-ASCII
    zip_name_encoded  = _url_quote(zip_name.encode('utf-8'), safe='')  # RFC 5987
    content_disposition = (
        f"attachment; "
        f"filename=\"{zip_name_ascii}\"; "
        f"filename*=UTF-8''{zip_name_encoded}"
    )

    return StreamingResponse(
        stream_zip(),
        media_type='application/zip',
        headers={
            'Content-Disposition': content_disposition,
            'Content-Length': str(zip_size),
        }
    )

# ── Queue ─────────────────────────────────────────────────────────────────────

@app.get("/api/queue")
async def queue():
    return get_queue()

# ── History ───────────────────────────────────────────────────────────────────

@app.get("/api/history")
async def history(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: str = Query(""),
    sort_by: str = Query("completed_at"),
    sort_dir: str = Query("desc"),
    platform: str = Query(""),
):
    db = get_db()
    try:
        offset = (page - 1) * per_page
        where = ["1=1"]
        params = []

        if search:
            where.append("(title LIKE ? OR artist LIKE ?)")
            params += [f"%{search}%", f"%{search}%"]
        if platform:
            where.append("platform = ?")
            params.append(platform)

        valid_sorts = {'completed_at', 'title', 'artist', 'platform', 'file_size'}
        if sort_by not in valid_sorts:
            sort_by = 'completed_at'
        sort_dir = 'DESC' if sort_dir.lower() != 'asc' else 'ASC'

        count_row = db.execute(
            f"SELECT COUNT(*) FROM history WHERE {' AND '.join(where)}", params
        ).fetchone()
        total = count_row[0] if count_row else 0

        rows = db.execute(
            f"""SELECT * FROM history
                WHERE {' AND '.join(where)}
                ORDER BY {sort_by} {sort_dir}
                LIMIT ? OFFSET ?""",
            params + [per_page, offset]
        ).fetchall()

        items = [row_to_dict(r) for r in rows]

        return {
            'items': items,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
        }
    finally:
        db.close()


@app.delete("/api/history")
async def clear_history():
    db = get_db()
    try:
        db.execute("DELETE FROM history")
        db.commit()
    finally:
        db.close()
    return {"cleared": True}


# ── Statistics ────────────────────────────────────────────────────────────────

@app.get("/api/statistics")
async def statistics():
    db = get_db()
    try:
        total_row = db.execute(
            "SELECT COUNT(*) as count, SUM(file_size) as size FROM history"
        ).fetchone()

        platform_rows = db.execute(
            """SELECT platform, COUNT(*) as count
               FROM history GROUP BY platform ORDER BY count DESC"""
        ).fetchall()

        artist_rows = db.execute(
            """SELECT artist, COUNT(*) as count
               FROM history GROUP BY artist ORDER BY count DESC LIMIT 10"""
        ).fetchall()

        # Last 7 days activity
        activity = []
        for i in range(6, -1, -1):
            day = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            row = db.execute(
                "SELECT COUNT(*) as count FROM history WHERE DATE(completed_at) = ?",
                (day,)
            ).fetchone()
            activity.append({'date': day, 'count': row[0] if row else 0})

        top_tracks = db.execute(
            """SELECT title, artist, COUNT(*) as downloads
               FROM history GROUP BY title, artist
               ORDER BY downloads DESC LIMIT 5"""
        ).fetchall()

        # Storage — exclude temp files (_tmp_*) and cover cache (.cover_cache)
        download_dir = Path(settings.UPLOAD_FOLDER)
        total_storage = sum(
            f.stat().st_size
            for f in download_dir.rglob('*')
            if (f.is_file()
                and not f.name.startswith('.')
                and not f.name.startswith('_tmp_')
                and '.cover_cache' not in f.parts)
        ) if download_dir.exists() else 0

        return {
            'total_downloads': total_row['count'] or 0,
            'total_size': total_row['size'] or 0,
            'total_size_display': format_file_size(total_row['size'] or 0),
            'storage_usage': total_storage,
            'storage_display': format_file_size(total_storage),
            'by_platform': [dict(r) for r in platform_rows],
            'top_artists': [dict(r) for r in artist_rows],
            'activity_7d': activity,
            'top_tracks': [dict(r) for r in top_tracks],
        }
    finally:
        db.close()

# ── Backup ────────────────────────────────────────────────────────────────────

@app.post("/api/backup")
async def backup(req: BackupRequest):
    result = await create_backup(req.selection, req.date_from, req.date_to)
    return result


@app.get("/api/backup")
async def list_backups():
    return get_backup_list()


@app.delete("/api/backup/{backup_id}")
async def del_backup(backup_id: str):
    ok = delete_backup(backup_id)
    return {"deleted": ok}


@app.get("/api/backup/{backup_id}/download")
async def download_backup(backup_id: str):
    db = get_db()
    try:
        row = db.execute("SELECT path, filename FROM backups WHERE id = ?", (backup_id,)).fetchone()
    finally:
        db.close()

    if not row:
        raise HTTPException(404, "Backup not found")
    path = row['path']
    if not os.path.exists(path):
        raise HTTPException(404, "Backup file missing")

    return FileResponse(path=path, filename=row['filename'], media_type='application/zip')

# ── Watched Folder ────────────────────────────────────────────────────────────

@app.get("/api/watched")
async def watched_status():
    return get_watched_status()

# ── Cover Preview ─────────────────────────────────────────────────────────────

# ── Thumbnail proxy (avoids browser CORS on external CDN URLs) ───────────────

@app.get("/api/thumbnail")
async def thumbnail_proxy(url: str = Query(...)):
    """
    Proxy an external thumbnail URL through the backend.
    Bypasses CORS blocks from SoundCloud/Bandcamp/YouTube CDNs.
    """
    import aiohttp

    if not url.startswith(('http://', 'https://')):
        raise HTTPException(400, "Invalid URL")

    try:
        from urllib.parse import urlparse as _urlparse
        parsed = _urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else url

        # Use a real browser User-Agent — some CDNs reject library agents
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept': 'image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': origin + '/',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'cross-site',
        }

        connector = aiohttp.TCPConnector(ssl=False)  # Some CDNs have cert issues
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    app_logger.warning(f"Thumbnail proxy: upstream {resp.status} for {url}")
                    raise HTTPException(502, f"Could not load thumbnail (upstream: {resp.status})")

                content_type = resp.headers.get('Content-Type', 'image/jpeg').split(';')[0].strip()
                # Ensure it's actually an image type
                if not content_type.startswith('image/'):
                    content_type = 'image/jpeg'
                data = await resp.read()

        return StreamingResponse(
            iter([data]),
            media_type=content_type,
            headers={
                'Cache-Control': 'public, max-age=3600',
                'Access-Control-Allow-Origin': '*',
            }
        )
    except HTTPException:
        raise
    except aiohttp.ClientError as e:
        app_logger.warning(f"Thumbnail proxy error for {url}: {e}")
        raise HTTPException(502, f"Could not fetch thumbnail: {e}")


class CoverPreviewRequest(BaseModel):
    thumbnail_url: str
    ratio: str = "1:1"
    resolution: str = "medium"

@app.post("/api/cover/preview")
async def cover_preview(req: CoverPreviewRequest):
    from backend.image_processor import (
        download_cover_image, process_cover_image, get_target_dimensions
    )
    import tempfile, imghdr

    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_f:
        tmp_path = tmp_f.name

    cover = None
    try:
        cover = await download_cover_image(req.thumbnail_url, tmp_path)
        if not cover:
            raise HTTPException(400, "Could not download thumbnail")

        processed = await process_cover_image(cover, req.ratio, req.resolution)
        w, h = get_target_dimensions(req.ratio, req.resolution)

        import base64
        with open(processed, 'rb') as f:
            raw = f.read()
            b64 = base64.b64encode(raw).decode()

        # Bug fix: detect actual image type so the data URI MIME type is correct.
        # process_cover_image saves as JPEG when it processes, but if resolution='original'
        # the original file (which could be .webp or .png) is returned as-is.
        ext = os.path.splitext(processed)[1].lower()
        mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif'}
        mime_type = mime_map.get(ext)
        if not mime_type:
            # Fall back to sniffing the raw bytes
            detected = imghdr.what(None, h=raw[:32])
            mime_type = f'image/{detected}' if detected else 'image/jpeg'

        size = os.path.getsize(processed)

        return {
            'preview': f"data:{mime_type};base64,{b64}",
            'dimensions': f"{w}x{h}" if w else "original",
            'size': format_file_size(size),
        }
    finally:
        if cover and os.path.exists(cover):
            try:
                os.remove(cover)
            except Exception:
                pass

# ── Downloads list ────────────────────────────────────────────────────────────

@app.get("/api/downloads")
async def list_downloads(
    status: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(50)
):
    db = get_db()
    try:
        where = "1=1"
        params = []
        if status:
            where = "status = ?"
            params.append(status)

        offset = (page - 1) * per_page
        rows = db.execute(
            f"SELECT * FROM downloads WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [per_page, offset]
        ).fetchall()

        from backend.queue_manager import download_states
        items = []
        for row in rows:
            item = row_to_dict(row)
            if item['id'] in download_states:
                item.update(download_states[item['id']])
            items.append(item)

        return items
    finally:
        db.close()

# ── Serve Frontend ────────────────────────────────────────────────────────────

frontend_path = Path(__file__).parent.parent / "frontend"

if frontend_path.exists():
    # Mount static directories for assets and components
    app.mount("/assets", StaticFiles(directory=str(frontend_path / "assets")), name="assets")
    app.mount("/components", StaticFiles(directory=str(frontend_path / "components")), name="components")

    @app.get("/styles.css")
    async def styles():
        return FileResponse(str(frontend_path / "styles.css"), media_type="text/css")

    @app.get("/app.js")
    async def appjs():
        return FileResponse(str(frontend_path / "app.js"), media_type="application/javascript")

    # Catch-all SPA route — must be LAST
    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str = ""):
        # API routes are registered before this catch-all, so they take priority.
        # Any unknown path returns index.html for client-side routing.
        return FileResponse(str(frontend_path / "index.html"))
