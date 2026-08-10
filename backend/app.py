import asyncio
import json
import os
import re
import time
import uuid
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Literal

import aiohttp
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Security, Depends, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, validator

from backend.config import settings, validate_settings
from backend.db_models import init_db, get_db, row_to_dict
from backend.logger import app_logger
from backend.metadata_handler import extract_metadata
from backend.queue_manager import (
    enqueue_download, cancel_download, get_download_status, get_queue, _cleanup_tasks
)
from backend.backup_manager import create_backup, get_backup_list, delete_backup
from backend.scheduler import start_scheduler
from backend.utils.validators import validate_url, is_valid_uuid, validate_bitrate
from backend.utils.file_organizer import format_file_size, format_duration
from backend.utils.ffmpeg_handler import is_ffmpeg_available

# ── Auth ──────────────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)
_API_TOKEN = os.environ.get("MEDIAN_API_TOKEN", "")


def require_token(creds: HTTPAuthorizationCredentials = Security(_bearer)):
    if not _API_TOKEN:
        return
    if not creds or not secrets.compare_digest(creds.credentials, _API_TOKEN):
        raise HTTPException(401, "Unauthorized")


# ── Rate limiting ─────────────────────────────────────────────────────────────

_rl_store: dict[str, list] = {}
_RL_LIMIT = 60
_RL_WINDOW = 60


def _rate_check(ip: str, limit: int = _RL_LIMIT, window: int = _RL_WINDOW) -> bool:
    now = time.time()
    # Evict IPs with no recent activity so the store doesn't grow unboundedly
    if len(_rl_store) > 1000:
        stale = [k for k, ts in _rl_store.items() if not ts or now - ts[-1] >= window]
        for k in stale:
            del _rl_store[k]
    recent = [t for t in _rl_store.get(ip, []) if now - t < window]
    if len(recent) >= limit:
        _rl_store[ip] = recent
        return False
    recent.append(now)
    _rl_store[ip] = recent
    return True


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Shared HTTP session ───────────────────────────────────────────────────────

_http_session: Optional[aiohttp.ClientSession] = None

CUSTOM_COVER_DIR = settings.custom_cover_path
ALLOWED_THUMBNAIL_HOSTS = settings.ALLOWED_THUMBNAIL_HOSTS


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_session

    validate_settings()

    app_logger.info("Initializing Median...")
    from backend.config import ensure_directories
    ensure_directories()
    init_db()
    start_scheduler()

    _http_session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        headers={"User-Agent": "Median/1.0"},
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "-U",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0:
            app_logger.info("yt-dlp is up to date")
        else:
            app_logger.warning(f"yt-dlp update check failed: {stderr.decode()}")
    except asyncio.TimeoutError:
        app_logger.warning("yt-dlp update timed out — skipping")
    except Exception as e:
        app_logger.warning(f"yt-dlp update skipped: {e}")

    app_logger.info("Median ready")

    yield

    for t in list(_cleanup_tasks):
        t.cancel()
    from backend.scheduler import scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)
    if _http_session and not _http_session.closed:
        await _http_session.close()
    app_logger.info("Median shut down cleanly")


app = FastAPI(title="Median", version="1.0.0", docs_url="/api/docs", lifespan=lifespan)

_cors_origins = settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ── Request models ────────────────────────────────────────────────────────────

class ValidateRequest(BaseModel):
    url: str

    @validator("url")
    def url_length(cls, v):
        if len(v) > settings.MAX_URL_LENGTH:
            raise ValueError(f"URL exceeds maximum length of {settings.MAX_URL_LENGTH}")
        return v


class CoverSettings(BaseModel):
    ratio: Literal["1:1", "16:9", "9:16", "4:3", "original"] = "1:1"
    resolution: Literal["low", "medium", "high", "original"] = "original"
    output_format: Literal["mp4", "mkv", "webm"] = "mp4"


class DownloadRequest(BaseModel):
    url: str
    download_type: Literal["audio", "video", "cover_audio"]
    format: Literal["mp3", "flac", "aac", "mp4", "mkv", "webm"]
    bitrate: Optional[str] = ""
    concatenate: bool = False
    crossfade: bool = False
    crossfade_duration: float = settings.CROSSFADE_DURATION
    cover_settings: Optional[CoverSettings] = None
    cover_id: Optional[str] = None
    include_description: bool = False
    # 1-based positions in the validated tracklist. None/empty means every
    # track — only a partial selection needs to be sent.
    selected_tracks: Optional[List[int]] = None

    @validator("url")
    def url_length(cls, v):
        if len(v) > settings.MAX_URL_LENGTH:
            raise ValueError(f"URL exceeds maximum length of {settings.MAX_URL_LENGTH}")
        return v

    @validator("selected_tracks")
    def selected_tracks_valid(cls, v):
        if not v:
            return None
        cleaned = sorted({i for i in v if isinstance(i, int) and i >= 1})
        if not cleaned:
            raise ValueError("selected_tracks must contain positive track numbers")
        if len(cleaned) > settings.MAX_PLAYLIST_TRACKS:
            raise ValueError(
                f"Too many tracks selected (max {settings.MAX_PLAYLIST_TRACKS})"
            )
        return cleaned

    @validator("crossfade_duration")
    def crossfade_duration_valid(cls, v):
        # Clamp to the configured bounds rather than reject — the UI slider stays
        # within range, but a stray value shouldn't fail the whole request.
        lo, hi = settings.CROSSFADE_MIN_DURATION, settings.CROSSFADE_MAX_DURATION
        return max(lo, min(hi, v))

    @validator("bitrate")
    def bitrate_valid(cls, v):
        if v:
            try:
                validate_bitrate(v)
            except ValueError as e:
                raise ValueError(str(e))
        return v

    @validator("cover_id")
    def cover_id_format(cls, v):
        if v and not is_valid_uuid(v):
            raise ValueError("cover_id must be a valid UUID")
        return v


class DiscographyRequest(BaseModel):
    url: str

    @validator("url")
    def url_length(cls, v):
        if len(v) > settings.MAX_URL_LENGTH:
            raise ValueError(f"URL exceeds maximum length of {settings.MAX_URL_LENGTH}")
        return v


class DiscographyAlbum(BaseModel):
    url: str
    title: str = ""

    @validator("url")
    def url_length(cls, v):
        if len(v) > settings.MAX_URL_LENGTH:
            raise ValueError(f"URL exceeds maximum length of {settings.MAX_URL_LENGTH}")
        return v

    @validator("title")
    def title_length(cls, v):
        return (v or "")[:300]


class DiscographyDownloadRequest(DownloadRequest):
    """Same options as a normal download, applied to every selected album.

    `url` stays the album the user validated — it identifies the artist and is
    the fallback when the client sends no explicit album selection.
    """
    albums: List[DiscographyAlbum] = []


class BackupRequest(BaseModel):
    selection: str = "all"
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class KeepFileRequest(BaseModel):
    keep: bool


class CoverPreviewRequest(BaseModel):
    thumbnail_url: Optional[str] = None
    cover_id: Optional[str] = None
    ratio: str = "1:1"
    resolution: str = "original"

    @validator("thumbnail_url")
    def must_be_allowed_host(cls, v):
        if v is None:
            return v
        from urllib.parse import urlparse
        p = urlparse(v)
        if p.scheme not in ("http", "https") or p.netloc not in settings.ALLOWED_THUMBNAIL_HOSTS:
            raise ValueError("thumbnail_url host not permitted")
        return v

    @validator("cover_id")
    def cover_id_format(cls, v):
        if v and not is_valid_uuid(v):
            raise ValueError("cover_id must be a valid UUID")
        return v


# ── Helpers ───────────────────────────────────────────────────────────────────

def _assert_within_upload_folder(path: Path):
    upload_root = Path(settings.UPLOAD_FOLDER).resolve()
    try:
        path.resolve().relative_to(upload_root)
    except ValueError:
        app_logger.warning(f"Access denied — path outside UPLOAD_FOLDER: {path}")
        raise HTTPException(403, "File access denied")


def _assert_within_cover_dir(path: Path):
    cover_root = CUSTOM_COVER_DIR.resolve()
    try:
        path.resolve().relative_to(cover_root)
    except ValueError:
        app_logger.warning(f"Access denied — path outside cover dir: {path}")
        raise HTTPException(400, "Invalid cover_id")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    import shutil
    db_ok = False
    try:
        db = get_db()
        db.execute("SELECT 1")
        db.close()
        db_ok = True
    except Exception:
        db_ok = False

    disk = shutil.disk_usage(settings.UPLOAD_FOLDER) if os.path.exists(settings.UPLOAD_FOLDER) else None

    from backend.queue_manager import active_downloads

    yt_dlp_version = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            yt_dlp_version = stdout.decode().strip()
    except Exception:
        pass

    status = "ok"
    if not db_ok:
        status = "degraded"
    if not is_ffmpeg_available():
        status = "degraded"

    return {
        "status": status,
        "ffmpeg": is_ffmpeg_available(),
        "db": db_ok,
        "disk_free_gb": round(disk.free / (1024**3), 2) if disk else 0,
        "yt_dlp_version": yt_dlp_version,
        "active_downloads": len(active_downloads),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/platforms")
async def platform_status():
    platforms = {
        "youtube":    "https://www.youtube.com",
        "soundcloud": "https://soundcloud.com",
        "bandcamp":   "https://bandcamp.com",
        # The embed player, not the main site — that is the only part Median
        # reads, and it stays up independently of open.spotify.com's app shell.
        "spotify":    "https://open.spotify.com/embed/track/4cOdK2wGLETKBW3PvgPWqT",
    }

    async def check_one(name: str, url: str) -> tuple:
        session = _http_session
        if not session:
            return name, False
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=5),
                allow_redirects=True
            ) as r:
                return name, r.status < 400
        except Exception:
            return name, False

    results = await asyncio.gather(
        *[check_one(name, url) for name, url in platforms.items()]
    )
    return dict(results)


@app.post("/api/validate")
async def validate(req: ValidateRequest, request: Request):
    ip = _get_client_ip(request)
    if not _rate_check(ip, limit=_RL_LIMIT, window=_RL_WINDOW):
        raise HTTPException(429, "Too many requests — please wait before trying again")

    is_valid, platform, error = validate_url(req.url, max_length=settings.MAX_URL_LENGTH)
    if not is_valid:
        raise HTTPException(400, error)

    meta = await extract_metadata(req.url)
    if 'error' in meta:
        raise HTTPException(422, meta['error'])

    meta['platform'] = platform
    if meta.get('duration'):
        meta['duration_display'] = format_duration(meta['duration'])
    if meta.get('total_duration'):
        meta['total_duration_display'] = format_duration(meta['total_duration'])

    return meta


def _apply_track_selection(meta: dict, selected: Optional[List[int]]):
    """Narrow an album's metadata to the tracks the user ticked.

    Returns (metadata, selected_indices). `selected_indices` are the original
    1-based positions, which the downloader hands to yt-dlp as `playlist_items`
    so only those tracks are fetched. The metadata's own track list is filtered
    to match, keeping tag/title lookups aligned with what actually downloads.
    """
    tracks = meta.get('tracks') or []
    if not selected or not meta.get('is_playlist') or not tracks:
        return meta, None

    indices = [i for i in selected if 1 <= i <= len(tracks)]
    if not indices:
        raise HTTPException(400, "No valid tracks selected")
    if len(indices) == len(tracks):
        return meta, None  # everything ticked — same as a normal download

    meta = dict(meta)
    meta['tracks'] = [tracks[i - 1] for i in indices]
    meta['track_count'] = len(indices)
    meta['total_duration'] = sum((t.get('duration') or 0) for t in meta['tracks'])
    return meta, indices


@app.post("/api/download", dependencies=[Depends(require_token)])
async def start_download(req: DownloadRequest, request: Request):
    ip = _get_client_ip(request)
    if not _rate_check(ip, limit=_RL_LIMIT, window=_RL_WINDOW):
        raise HTTPException(429, "Too many requests — please wait before trying again")

    from backend.utils.validators import detect_platform as _detect
    platform = _detect(req.url) or 'unknown'

    meta = await extract_metadata(req.url)
    if 'error' in meta:
        raise HTTPException(422, meta['error'])

    meta['platform'] = platform

    meta, selected_indices = _apply_track_selection(meta, req.selected_tracks)

    cover_settings_dict = req.cover_settings.dict() if req.cover_settings else None

    download_id = await enqueue_download({
        'url': req.url,
        'download_type': req.download_type,
        'format': req.format,
        'bitrate': req.bitrate,
        'concatenate': req.concatenate,
        'crossfade': req.crossfade,
        'crossfade_duration': req.crossfade_duration,
        'metadata': meta,
        'cover_settings': cover_settings_dict,
        'cover_id': req.cover_id,
        'include_description': req.include_description,
        'selected_indices': selected_indices,
    })

    return {
        'download_id': download_id,
        'status': 'queued',
        'title': meta.get('title', ''),
        'artist': meta.get('artist', ''),
    }


@app.post("/api/discography")
async def discography(req: DiscographyRequest, request: Request):
    """Every album by the artist behind this URL, for the album picker."""
    ip = _get_client_ip(request)
    if not _rate_check(ip, limit=_RL_LIMIT, window=_RL_WINDOW):
        raise HTTPException(429, "Too many requests — please wait before trying again")

    is_valid, platform, error = validate_url(req.url, max_length=settings.MAX_URL_LENGTH)
    if not is_valid:
        raise HTTPException(400, error)

    # Normally already cached by /api/validate. YouTube needs it for the
    # channel URL; the other platforms derive the artist page from the URL.
    meta = await extract_metadata(req.url)
    if 'error' in meta:
        meta = {}

    from backend.discography import resolve_discography
    result = await resolve_discography(req.url, meta)
    result['platform'] = platform
    if not result.get('artist'):
        result['artist'] = meta.get('artist', '')
    return result


@app.post("/api/discography/download", dependencies=[Depends(require_token)])
async def start_discography_download(req: DiscographyDownloadRequest, request: Request):
    """Queue one download per album, each landing in its own folder.

    Albums are queued with placeholder metadata and resolved individually by
    the queue — extracting every tracklist here would take minutes for a large
    discography. The response therefore returns immediately with one
    download_id per album.
    """
    ip = _get_client_ip(request)
    if not _rate_check(ip, limit=_RL_LIMIT, window=_RL_WINDOW):
        raise HTTPException(429, "Too many requests — please wait before trying again")

    from backend.discography import resolve_discography

    base_meta = await extract_metadata(req.url)
    if 'error' in base_meta:
        base_meta = {}

    albums = [{'url': a.url, 'title': a.title} for a in req.albums]
    if not albums:
        # No explicit selection — take the artist's whole discography.
        albums = (await resolve_discography(req.url, base_meta)).get('albums', [])

    if not albums:
        raise HTTPException(422, "No albums found for this artist")

    if len(albums) > settings.MAX_DISCOGRAPHY_ALBUMS:
        raise HTTPException(
            400,
            f"Too many albums selected (max {settings.MAX_DISCOGRAPHY_ALBUMS})"
        )

    cover_settings_dict = req.cover_settings.dict() if req.cover_settings else None
    artist = base_meta.get('artist', '')
    batch_id = str(uuid.uuid4())

    queued, skipped = [], []
    seen = set()
    for album in albums:
        album_url = (album.get('url') or '').strip()
        if not album_url or album_url in seen:
            continue
        seen.add(album_url)

        is_valid, platform, error = validate_url(album_url, max_length=settings.MAX_URL_LENGTH)
        if not is_valid:
            skipped.append({'url': album_url, 'reason': error})
            continue

        title = (album.get('title') or '').strip() or album_url
        download_id = await enqueue_download({
            'url': album_url,
            'download_type': req.download_type,
            'format': req.format,
            'bitrate': req.bitrate,
            'concatenate': req.concatenate,
            'crossfade': req.crossfade,
            'crossfade_duration': req.crossfade_duration,
            'cover_settings': cover_settings_dict,
            # A single uploaded cover can't stand in for a whole discography —
            # each album keeps its own artwork.
            'cover_id': None,
            'include_description': req.include_description,
            'source': 'discography',
            'batch_id': batch_id,
            # Held from auto-cleanup until the combined zip is fetched, so the
            # first albums don't expire while the last ones are still running.
            'keep_file': True,
            'metadata': {
                'is_playlist': True,
                'platform': platform,
                'title': title,
                'album': title,
                'artist': artist,
                'track_count': 0,
                'url': album_url,
                # Tells the queue to extract the real tracklist before starting.
                'needs_resolve': True,
            },
        })
        queued.append({'download_id': download_id, 'title': title, 'url': album_url})

    if not queued:
        raise HTTPException(422, "None of the selected albums could be queued")

    app_logger.info(
        f"Discography queued [{batch_id[:8]}]: {len(queued)} album(s) "
        f"for {artist or req.url}"
    )
    return {
        'artist': artist,
        'batch_id': batch_id,
        'queued': queued,
        'skipped': skipped,
    }


_TERMINAL_STATES = ('completed', 'error', 'cancelled', 'cleaned')


def _batch_rows(batch_id: str) -> list:
    if not is_valid_uuid(batch_id):
        raise HTTPException(400, "Invalid batch id")
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM downloads WHERE batch_id = ? ORDER BY created_at ASC",
            (batch_id,)
        ).fetchall()
    finally:
        db.close()
    if not rows:
        raise HTTPException(404, "Batch not found")
    return rows


def _within_upload_folder(path: Path) -> bool:
    """Containment check that reports rather than raises — a bulk purge should
    skip a suspect path, not abort over one bad row."""
    try:
        path.resolve().relative_to(Path(settings.UPLOAD_FOLDER).resolve())
        return True
    except (ValueError, OSError):
        return False


def _purgeable_rows(db) -> list:
    """Finished downloads that still have files on disk.

    Anything currently downloading is excluded: yt-dlp is mid-write, and
    pulling the file out from under it corrupts the download instead of
    freeing space.
    """
    from backend.queue_manager import active_downloads

    rows = db.execute(
        "SELECT id, file_path, file_size, title, album FROM downloads "
        "WHERE file_path IS NOT NULL AND file_path != ''"
    ).fetchall()

    out = []
    for row in rows:
        if row['id'] in active_downloads:
            continue
        path = Path(row['file_path'])
        if not _within_upload_folder(path) or not path.exists():
            continue
        out.append(row)
    return out


def _path_size(path: Path, fallback: int = 0) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
    except OSError:
        pass
    return fallback


def _orphan_entries() -> list:
    """Files in the download folder that no download record points at.

    Leftovers from an older install, a database reset, or a delete that half
    failed. Median has no record of them, so they are only ever removed on an
    explicit opt-in — never as part of the ordinary sweep.
    """
    root = Path(settings.UPLOAD_FOLDER)
    if not root.exists():
        return []

    known = set()
    db = get_db()
    try:
        rows = db.execute(
            "SELECT file_path FROM downloads "
            "WHERE file_path IS NOT NULL AND file_path != ''"
        ).fetchall()
    finally:
        db.close()

    for row in rows:
        try:
            known.add(Path(row['file_path']).resolve())
        except (OSError, ValueError):
            pass

    orphans = []
    for entry in root.iterdir():
        # Dot-entries are Median's own caches, not stray downloads
        if entry.name.startswith('.'):
            continue
        try:
            if entry.resolve() in known:
                continue
        except OSError:
            continue
        orphans.append(entry)
    return orphans


def _measure_cleanup() -> dict:
    """What a purge would free, without touching anything."""
    from backend.queue_manager import active_downloads

    db = get_db()
    try:
        rows = _purgeable_rows(db)
    finally:
        db.close()

    total = sum(_path_size(Path(r['file_path']), r['file_size'] or 0) for r in rows)

    orphans = _orphan_entries()
    orphan_bytes = sum(_path_size(p) for p in orphans)

    return {
        'items': len(rows),
        'bytes': total,
        'size': format_file_size(total),
        'orphans': len(orphans),
        'orphan_bytes': orphan_bytes,
        'orphan_size': format_file_size(orphan_bytes),
        'orphan_names': sorted(p.name for p in orphans)[:10],
        'active_downloads': len(active_downloads),
    }


def _run_cleanup(include_orphans: bool = False) -> dict:
    """Delete every finished download's files right now. Blocking."""
    from backend.queue_manager import active_downloads
    from backend.scheduler import _delete_download_path, _sweep_stale_partials

    db = get_db()
    try:
        rows = _purgeable_rows(db)
        freed, removed, ids = 0, 0, []

        for row in rows:
            size = _path_size(Path(row['file_path']), row['file_size'] or 0)
            if _delete_download_path(row['file_path']):
                ids.append(row['id'])
                freed += size
                removed += 1

        if ids:
            marks = ','.join('?' * len(ids))
            db.execute(
                f"UPDATE downloads SET status = 'cleaned', keep_file = 0 "
                f"WHERE id IN ({marks})",
                ids
            )
            db.commit()
    finally:
        db.close()

    # Unrecognised files, only on request. Skipped outright while anything is
    # downloading: a download in flight has no file_path recorded until it
    # finishes, so its half-written folder would look exactly like an orphan.
    orphans_removed = 0
    if include_orphans and not active_downloads:
        for entry in _orphan_entries():
            size = _path_size(entry)
            if _delete_download_path(str(entry)):
                orphans_removed += 1
                freed += size

    # Sweep yt-dlp's leftovers too. With nothing running, everything temporary
    # is orphaned by definition and can go regardless of age; otherwise fall
    # back to the age-based sweep so an in-flight download keeps its .part.
    partials = _sweep_stale_partials(max_age_seconds=0 if not active_downloads else None)

    return {
        'removed': removed,
        'orphans_removed': orphans_removed,
        'partials_removed': partials,
        'freed_bytes': freed,
        'freed': format_file_size(freed),
        'skipped_active': len(active_downloads),
    }


@app.get("/api/cleanup/preview")
async def cleanup_preview():
    """How much a "clean now" would reclaim, so the UI can confirm with real
    numbers instead of asking the user to delete something unspecified."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _measure_cleanup)


@app.post("/api/cleanup/now", dependencies=[Depends(require_token)])
async def cleanup_now(request: Request, include_orphans: bool = Query(False)):
    """Delete every finished download immediately, without waiting for the
    retention window — for when the disk fills up before cleanup is due.

    `include_orphans` additionally removes files in the download folder that
    Median has no record of. Off by default: those are unrecognised, so
    deleting them is the user's explicit call, and it is ignored entirely
    while anything is still downloading.
    """
    # Namespaced bucket: the shared per-IP counter is spent by ordinary
    # downloading, and a busy hour must not lock you out of freeing disk space.
    ip = _get_client_ip(request)
    if not _rate_check(f"cleanup:{ip}", limit=10, window=_RL_WINDOW):
        raise HTTPException(429, "Too many requests — please wait before trying again")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _run_cleanup, include_orphans)
    app_logger.info(
        f"Manual cleanup: removed {result['removed']} download(s), "
        f"{result['orphans_removed']} unrecognised file(s) and "
        f"{result['partials_removed']} leftover(s), freed {result['freed']}"
    )
    return result


@app.get("/api/discography/batches")
async def discography_batches():
    """Discography batches still worth showing — running, or finished but not
    yet collected.

    Without this the combined-zip button would live only in the tab that
    started the batch: a refresh, or coming back later (which is normal, since
    a large discography runs for hours) would leave no way to reach the zip.
    """
    db = get_db()
    try:
        rows = db.execute("""
            SELECT batch_id,
                   MAX(artist) AS artist,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status IN ('queued','downloading') THEN 1 ELSE 0 END) AS running,
                   SUM(CASE WHEN keep_file = 1 THEN 1 ELSE 0 END) AS held,
                   SUM(CASE WHEN collected_at IS NOT NULL THEN 1 ELSE 0 END) AS collected,
                   SUM(COALESCE(file_size, 0)) AS total_size,
                   MAX(COALESCE(completed_at, created_at)) AS last_activity
            FROM downloads
            WHERE batch_id IS NOT NULL
            GROUP BY batch_id
            HAVING (running > 0 OR held > 0) AND collected = 0
            ORDER BY last_activity DESC
            LIMIT 10
        """).fetchall()
    finally:
        db.close()

    return {'batches': [{
        'batch_id': r['batch_id'],
        'artist': r['artist'] or '',
        'total': r['total'],
        'completed': r['completed'],
        'failed': r['total'] - r['completed'] - r['running'],
        'in_progress': r['running'],
        'all_done': r['running'] == 0,
        'total_size': r['total_size'] or 0,
    } for r in rows]}


@app.get("/api/discography/batch/{batch_id}")
async def discography_batch(batch_id: str):
    """Progress of one 'download every album' click.

    The combined zip is only worth offering once every album has settled, so
    the UI polls this to know when to show the button.
    """
    rows = _batch_rows(batch_id)
    albums = [{
        'download_id': r['id'],
        'title': r['title'] or r['album'] or '',
        'status': r['status'],
        'file_size': r['file_size'] or 0,
    } for r in rows]

    done = [a for a in albums if a['status'] in _TERMINAL_STATES]
    ready = [a for a in albums if a['status'] == 'completed']
    return {
        'batch_id': batch_id,
        'artist': rows[0]['artist'] or '',
        'total': len(albums),
        'finished': len(done),
        'completed': len(ready),
        'failed': len(done) - len(ready),
        'in_progress': len(albums) - len(done),
        'all_done': len(done) == len(albums),
        'total_size': sum(a['file_size'] for a in ready),
        'albums': albums,
    }


class _ZipSink:
    """File-like target that hands whatever zipfile writes straight back out.

    Lets the archive be streamed as it is built instead of staged to a temp
    file first — a discography can run to a couple of gigabytes, and writing
    that to disk before sending a byte would both stall the browser and need
    the space twice over.
    """

    def __init__(self):
        self._buf = bytearray()
        self._pos = 0

    def write(self, data) -> int:
        self._buf += data
        self._pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self._pos

    def flush(self):
        pass

    def seekable(self) -> bool:
        return False

    def close(self):
        pass

    def drain(self) -> bytes:
        chunk = bytes(self._buf)
        del self._buf[:]
        return chunk


def _short_error(message: str) -> str:
    """Trim yt-dlp's 'please report this issue on ...' boilerplate, which buries
    the one useful clause in a wall of text."""
    text = (message or '').strip()
    for marker in ('; please report', 'please report this issue'):
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
            break
    return text[:200].strip().rstrip(';')


def _album_folder(row) -> str:
    """The album's folder name inside the combined zip. Shared so the audio and
    the description.md beside it can never disagree about where they belong."""
    from backend.utils.validators import sanitize_filename

    return sanitize_filename(row['album'] or row['title'] or 'Album') or 'Album'


def _album_zip_entries(row) -> list:
    """(source path, path inside the zip) for one album in the combined zip."""
    raw = row['file_path']
    if not raw:
        return []
    path = Path(raw).resolve()
    try:
        _assert_within_upload_folder(path)
    except HTTPException:
        return []
    if not path.exists():
        return []

    folder = _album_folder(row)
    if path.is_file():
        # A merged album is a single file — still give it its own folder so
        # every album unzips the same way.
        return [(path, f"{folder}/{_title_only(path.stem)}{path.suffix}")]

    return [
        (f, f"{folder}/{_title_only(f.stem)}{f.suffix}")
        for f in sorted(path.iterdir())
        if f.is_file() and not f.name.startswith('.')
    ]


@app.get("/api/discography/batch/{batch_id}/file")
async def get_batch_file(batch_id: str):
    """Every finished album of a batch as one zip, a folder per album.

    Streamed as it is built. Albums that failed are listed in a README inside
    the archive rather than silently omitted.
    """
    import zipfile

    rows = _batch_rows(batch_id)
    completed = [r for r in rows if r['status'] == 'completed']
    if not completed:
        raise HTTPException(404, "No completed albums in this batch yet")

    # Grouped by album rather than one flat list, so each folder's description.md
    # can be written into it — a whole-discography download asks for the option
    # once and expects it applied to every album, not just the ones fetched
    # individually.
    plan, included = [], []
    for row in completed:
        album_entries = _album_zip_entries(row)
        if album_entries:
            plan.append((row, album_entries))
            included.append(row['album'] or row['title'] or 'Album')

    if not plan:
        raise HTTPException(410, "Album files have been cleaned up — please download again.")

    missing = [
        f"{r['album'] or r['title'] or 'Album'} — {r['status']}"
        f"{': ' + _short_error(r['error_message']) if r['error_message'] else ''}"
        for r in rows if r['status'] != 'completed'
    ]

    from backend.utils.validators import sanitize_filename
    # Just the band name — the folders inside already say what it is, so any
    # extra wording only gets in the way when filing it away.
    artist = sanitize_filename(rows[0]['artist'] or '') or 'Discography'
    zip_name = f"{artist}.zip"

    async def stream():
        loop = asyncio.get_running_loop()
        sink = _ZipSink()
        # ZIP_STORED: audio is already compressed, so deflating it burns CPU
        # for nothing — this is purely a container.
        zf = zipfile.ZipFile(sink, 'w', zipfile.ZIP_STORED)
        try:
            for row, album_entries in plan:
                for src, arcname in album_entries:
                    try:
                        handle = open(str(src), 'rb')
                    except OSError as e:
                        app_logger.warning(f"Batch zip skipped {src}: {e}")
                        continue
                    try:
                        # Carry the real file time across — zipfile otherwise
                        # stamps every entry 1980-01-01, which looks broken once
                        # extracted and confuses library "date added" sorting.
                        try:
                            stamp = time.localtime(src.stat().st_mtime)[:6]
                        except OSError:
                            stamp = time.localtime()[:6]
                        info = zipfile.ZipInfo(arcname, date_time=stamp)
                        info.compress_type = zipfile.ZIP_STORED
                        with zf.open(info, 'w') as dest:
                            while True:
                                # File I/O in a worker thread — a multi-GB archive
                                # would otherwise block the loop for the whole
                                # transfer, stalling progress streams and the
                                # container healthcheck.
                                data = await loop.run_in_executor(None, handle.read, 65536)
                                if not data:
                                    break
                                await loop.run_in_executor(None, dest.write, data)
                                out = sink.drain()
                                if out:
                                    yield out
                    finally:
                        handle.close()
                    out = sink.drain()
                    if out:
                        yield out

                # The album's own description.md, in its folder beside the audio.
                if _wants_description(row):
                    try:
                        chapters = []
                        if row['is_concatenated']:
                            merged = Path(row['file_path']).resolve()
                            if merged.is_file():
                                chapters = await loop.run_in_executor(
                                    None, _read_file_chapters, merged
                                )
                        zf.writestr(
                            f"{_album_folder(row)}/description.md",
                            _safe_description_md(row, chapters),
                        )
                    except Exception as e:
                        # Not fatal, and not lost either: the sweep below writes
                        # the short form for anything that failed here.
                        app_logger.warning(
                            f"Batch zip description failed for {_album_folder(row)}: {e}"
                        )
                    out = sink.drain()
                    if out:
                        yield out

            # Double-check every requested description actually made it in.
            # It is the one thing in the archive with no file on disk behind it,
            # so a failure above would leave the album silently without one —
            # precisely the gap this pass exists to close. Reading back what the
            # archive really contains beats trusting that the writes worked.
            present = set(zf.namelist())
            for row, _ in plan:
                name = f"{_album_folder(row)}/description.md"
                if not _wants_description(row) or name in present:
                    continue
                app_logger.warning(f"Batch zip: {name} was missing — writing it again")
                zf.writestr(name, _safe_description_md(row, []))
                present.add(name)
                out = sink.drain()
                if out:
                    yield out

            if missing:
                note = (
                    "Some albums are not in this archive:\n\n"
                    + "\n".join(f"  - {m}" for m in missing)
                    + "\n\nRe-queue them from Median to try again.\n"
                )
                zf.writestr("MISSING ALBUMS.txt", note)
        finally:
            zf.close()

        out = sink.drain()
        if out:
            yield out

        # Only reached once the whole archive has been written to the client —
        # a cancelled download leaves the batch untouched and collectable.
        await loop.run_in_executor(None, _mark_batch_collected, batch_id)

    app_logger.info(
        f"Streaming batch zip [{batch_id[:8]}]: {len(included)} album(s), "
        f"{len(missing)} missing"
    )
    return StreamingResponse(
        stream(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


def _mark_batch_collected(batch_id: str):
    """Stamp a batch as collected so the short-timer sweep can reclaim it.

    The keep flag deliberately stays set: it hands the batch to the dedicated
    collected-batch job rather than the normal retention sweep, so the delay is
    the configured few minutes instead of whenever the next hourly pass lands.
    """
    db = get_db()
    try:
        db.execute(
            "UPDATE downloads SET collected_at = datetime('now') WHERE batch_id = ?",
            (batch_id,)
        )
        db.commit()
        app_logger.info(
            f"Batch {batch_id[:8]} collected — files removed in "
            f"{settings.BATCH_DELETE_MINUTES} min"
        )
    except Exception as e:
        app_logger.warning(f"Could not mark batch {batch_id} collected: {e}")
    finally:
        db.close()


@app.get("/api/downloads/status")
async def downloads_status(ids: str = Query(..., max_length=4096)):
    """Status for many downloads in one request.

    A discography batch queues one download per album, and browsers only allow
    ~6 concurrent connections per host — opening an SSE stream per album would
    stall every other request the page makes. Batches poll this instead.
    Unknown ids are simply absent from the response.
    """
    wanted = [i.strip() for i in ids.split(',') if i.strip()]
    if len(wanted) > settings.MAX_DISCOGRAPHY_ALBUMS:
        raise HTTPException(400, "Too many ids requested")

    result = {}
    for download_id in wanted:
        if not is_valid_uuid(download_id):
            continue
        state = get_download_status(download_id)
        if state:
            result[download_id] = state
    return result


@app.get("/api/download/{download_id}/status")
async def download_status(download_id: str):
    status = get_download_status(download_id)
    if not status:
        raise HTTPException(404, "Download not found")
    return status


@app.get("/api/download/{download_id}/events")
async def download_events(download_id: str, request: Request):
    async def generator():
        last_payload = None
        # Safety cap so a stuck download doesn't keep the connection forever
        max_iterations = 60 * 60 * 5  # 0.2s * this = 1 hour
        for _ in range(max_iterations):
            if await request.is_disconnected():
                break
            state = get_download_status(download_id)
            if not state:
                yield f"data: {json.dumps({'status': 'not_found'})}\n\n"
                break
            payload = json.dumps(state, sort_keys=True, default=str)
            if payload != last_payload:
                last_payload = payload
                yield f"data: {payload}\n\n"
            if state.get('status') in ('completed', 'error', 'cancelled', 'cleaned'):
                break
            await asyncio.sleep(0.2)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


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


def _title_only(raw: str) -> str:
    s = raw.strip()

    s = re.sub(r'^\d+[\s_\-\.]+', '', s).strip()

    if '_-_' in s:
        parts = s.split('_-_', 1)
        if parts[1].strip('_').strip():
            s = parts[1].strip('_').strip()

    elif ' - ' in s:
        parts = s.split(' - ', 1)
        if parts[1].strip():
            s = parts[1].strip()

    elif '_' in s and ' ' not in s:
        candidate = re.sub(r'^[^_]+_', '', s, count=1).strip('_').strip()
        if len(candidate) >= 3:
            s = candidate

    s = s.replace('_', ' ').strip()
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', s).strip()

    return s if s else raw.replace('_', ' ').strip()


def _chapter_ts(seconds: float) -> str:
    total = int(seconds)
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _read_file_chapters(file_path: Path) -> list:
    """Embedded chapters with YouTube-ready timestamps. Crossfade correction is
    inherent: the times come from the merged file itself."""
    from backend.utils.ffmpeg_handler import get_media_chapters
    return [
        {'time': _chapter_ts(c['start']), 'start': c['start'], 'title': c['title']}
        for c in get_media_chapters(str(file_path))
    ]


_PLATFORM_LABELS = {'youtube': 'YouTube', 'soundcloud': 'SoundCloud', 'bandcamp': 'Bandcamp'}


def _format_release_date(raw) -> str:
    """YYYYMMDD → 'March 14, 2011' (the way Bandcamp displays it)."""
    digits = ''.join(c for c in str(raw or '') if c.isdigit())
    if len(digits) < 8:
        return ''
    try:
        d = datetime.strptime(digits[:8], '%Y%m%d')
        return f"{d:%B} {d.day}, {d.year}"
    except ValueError:
        return ''


def _artist_page_url(row) -> str:
    """The artist's page (not the album page): stored channel/uploader URL when
    the platform reports one, otherwise derived from the download URL."""
    from urllib.parse import urlparse

    stored = (row['artist_url'] if 'artist_url' in row.keys() else '') or ''
    if stored.startswith('http'):
        return stored
    url = (row['url'] or '').strip()
    p = urlparse(url)
    if not p.netloc:
        return url
    if row['platform'] == 'bandcamp':
        return f"{p.scheme}://{p.netloc}"           # artist is the subdomain
    if row['platform'] == 'soundcloud':
        segments = [s for s in p.path.split('/') if s]
        if segments:
            return f"{p.scheme}://{p.netloc}/{segments[0]}"
    return url


def _row_json_list(row, column: str) -> list:
    try:
        raw = row[column] if column in row.keys() else None
        parsed = json.loads(raw) if raw else []
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _description_footer(row, artist: str) -> list:
    """Source link, release date, hashtags and the credits/disclaimer —
    shared by the album-wide layout and each per-track block."""
    lines = []
    url = (row['url'] or '').strip()
    if url.startswith('http'):
        label = _PLATFORM_LABELS.get(row['platform'], (row['platform'] or 'Source').title())
        lines.append(f"{label} : {url}")
    released = _format_release_date(row['release_date'] if 'release_date' in row.keys() else '')
    if released:
        lines.append(f"Released : {released}")

    # Normalize tags to hashtag form and dedupe (Bandcamp often repeats
    # tags with case variants), keeping first-seen order.
    hashtags = []
    for t in _row_json_list(row, 'tags'):
        h = '#' + re.sub(r'[^0-9a-z]+', '', str(t).lower())
        if len(h) > 1 and h not in hashtags:
            hashtags.append(h)
    if hashtags:
        lines += ([""] if lines else []) + [" ".join(hashtags)]

    support_url = _artist_page_url(row) or 'the original release page'
    lines += ([""] if lines else []) + [(
        f"No copyright infringement intended. All credit and rights belong to {artist}. "
        f"Please support the original release here: {support_url}"
    )]
    return lines


def _build_description_md(row, chapters: list) -> str:
    artist = row['artist'] or 'Unknown Artist'
    album = row['album'] or row['title'] or 'Album'
    lyrics = [
        e for e in _row_json_list(row, 'lyrics')
        if (e.get('lyrics') or '').strip()
    ]
    footer = _description_footer(row, artist)

    # Separate-track downloads with lyrics: one self-contained block per song
    # ("## title" + Lyrics + source/tags/credits), ready to paste per upload.
    if not chapters and lyrics:
        blocks = []
        for entry in lyrics:
            l_title = (entry.get('title') or '?').strip()
            blocks.append("\n".join(
                [f"## {l_title}", "Lyrics:", "", entry['lyrics'].strip(), ""] + footer
            ))
        return "\n\n".join(blocks) + "\n"

    # Merged albums (and downloads without lyrics): album-wide layout.
    lines = [f"# {artist} / {album}"]
    if chapters:
        lines += ["", "-- TRACKLIST --"]
        lines += [f"{c['time']} - {c['title'] or '?'}" for c in chapters]

    if lyrics:
        time_by_title = {
            (c['title'] or '').strip().lower(): c['time'] for c in chapters
        }
        lines += ["", "-- LYRICS --"]
        for entry in lyrics:
            l_title = (entry.get('title') or '?').strip()
            t = time_by_title.get(l_title.lower())
            header = f"====== {t} - {l_title} ======" if t else f"====== {l_title} ======"
            lines += ["", header, "", entry['lyrics'].strip()]

    lines += [""] + footer
    return "\n".join(lines) + "\n"


def _wants_description(row) -> bool:
    """Did this download ask for a description.md?"""
    return bool(row['include_description'] if 'include_description' in row.keys() else 0)


def _safe_description_md(row, chapters: list) -> str:
    """description.md for one album, guaranteed to return text.

    The description is generated rather than copied, so unlike the audio beside
    it there is no file on disk to fall back on — one bad row (unparseable
    lyrics JSON, a NULL where a string was assumed) would otherwise take the
    whole archive down with it, or worse, quietly leave the album without one.

    A description carrying only the artist, album and source link is still worth
    having, so a failure degrades to that instead of to nothing.
    """
    try:
        text = _build_description_md(row, chapters)
        if text and text.strip():
            return text
        app_logger.warning(
            f"Empty description for {row['album'] or row['title']} — using the short form"
        )
    except Exception as e:
        app_logger.warning(
            f"Description build failed for {row['album'] or row['title']} "
            f"({e}) — using the short form"
        )

    artist = row['artist'] or 'Unknown Artist'
    album = row['album'] or row['title'] or 'Album'
    lines = [f"# {artist} / {album}"]
    url = (row['url'] or '').strip()
    if url.startswith('http'):
        label = _PLATFORM_LABELS.get(row['platform'], (row['platform'] or 'Source').title())
        lines += ["", f"{label} : {url}"]
    lines += ["", (
        f"No copyright infringement intended. All credit and rights belong to {artist}. "
        f"Please support the original release."
    )]
    return "\n".join(lines) + "\n"


def _get_download_row_and_file(download_id: str, allow_dir: bool = False):
    db = get_db()
    try:
        row = db.execute(
            "SELECT * FROM downloads WHERE id = ?", (download_id,)
        ).fetchone()
    finally:
        db.close()
    if not row or not row['file_path']:
        raise HTTPException(404, "Download not found")
    file_path = Path(row['file_path']).resolve()
    _assert_within_upload_folder(file_path)
    if not (file_path.is_file() or (allow_dir and file_path.is_dir())):
        raise HTTPException(410, "File has been cleaned up")
    return row, file_path


@app.get("/api/download/{download_id}/chapters")
async def download_chapters(download_id: str):
    """Embedded chapters of a merged download, with YouTube-ready timestamps."""
    _, file_path = _get_download_row_and_file(download_id)
    loop = asyncio.get_running_loop()
    chapters = await loop.run_in_executor(None, _read_file_chapters, file_path)
    return {'chapters': chapters}


@app.get("/api/download/{download_id}/description.md")
async def download_description_md(download_id: str):
    """Markdown description: heading, tracklist (merged albums only), source
    link, release date, hashtags and a credit/disclaimer line."""
    from fastapi.responses import Response

    # allow_dir: separate-track albums are folders — their description has no
    # tracklist/timestamps, but source, lyrics, tags and credits still apply.
    row, file_path = _get_download_row_and_file(download_id, allow_dir=True)
    chapters = []
    if row['is_concatenated'] and file_path.is_file():
        loop = asyncio.get_running_loop()
        chapters = await loop.run_in_executor(None, _read_file_chapters, file_path)

    md = _safe_description_md(row, chapters)
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="description.md"'},
    )


@app.get("/api/download/{download_id}/file")
async def get_file(download_id: str):
    import zipfile, tempfile
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

    file_path = Path(row['file_path']).resolve()

    # Ensure the path is within the designated download folder
    _assert_within_upload_folder(file_path)

    if not file_path.exists():
        raise HTTPException(410, "File has been cleaned up — please download again.")

    artist = sanitize_filename(row['artist'] or '') or 'Unknown'
    title  = sanitize_filename(row['title']  or '') or 'Download'
    album  = sanitize_filename(row['album']  or row['title'] or '') or 'Album'
    is_playlist = bool(row['is_playlist'])

    include_description = _wants_description(row)

    # A lone file with nothing to bundle beside it doesn't need an archive.
    # Zipping it just forces an extract step before the track will play. An
    # album kept as separate tracks is a directory (browsers can't download
    # one), and description.md makes a second file — those still need the zip.
    if file_path.is_file() and not include_description:
        stem = album if is_playlist else title
        return FileResponse(
            str(file_path),
            filename=f"{artist} - {stem}{file_path.suffix}",
        )

    zip_name = f"{artist} - {album}.zip" if is_playlist else f"{artist} - {title}.zip"

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

                # Opt-in description.md. The tracklist section only exists for
                # merged single files (chapters live in the file); separate-track
                # downloads get the source/credits/hashtags without a tracklist.
                if include_description:
                    chapters = []
                    if row['is_concatenated'] and file_path.is_file():
                        chapters = _read_file_chapters(file_path)
                    zf.writestr('description.md', _safe_description_md(row, chapters))
        except FileNotFoundError:
            raise

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

    from urllib.parse import quote as _url_quote
    zip_name_ascii    = zip_name.encode('ascii', 'replace').decode()
    zip_name_encoded  = _url_quote(zip_name.encode('utf-8'), safe='')
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


@app.get("/api/download/{download_id}/tracks")
async def list_tracks(download_id: str):
    db = get_db()
    try:
        row = db.execute(
            "SELECT file_path, is_playlist, is_concatenated FROM downloads WHERE id = ?",
            (download_id,),
        ).fetchone()
    finally:
        db.close()

    if not row or not row['file_path']:
        raise HTTPException(404, "Download not found")

    if row['is_concatenated']:
        raise HTTPException(
            400, "Download was concatenated into a single file — use Download File instead"
        )

    folder = Path(row['file_path']).resolve()
    _assert_within_upload_folder(folder)

    if not folder.is_dir():
        raise HTTPException(400, "This download is not a multi-track folder")

    MEDIA_EXTS = {'.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.mp4', '.mkv', '.webm'}
    files = sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in MEDIA_EXTS
    )
    return [
        {"name": f.name, "stem": _title_only(f.stem), "size": f.stat().st_size, "index": i + 1}
        for i, f in enumerate(files)
    ]


@app.get("/api/download/{download_id}/track/{filename:path}")
async def get_single_track(download_id: str, filename: str):
    from urllib.parse import quote as _url_quote

    db = get_db()
    try:
        row = db.execute(
            "SELECT file_path FROM downloads WHERE id = ?", (download_id,)
        ).fetchone()
    finally:
        db.close()

    if not row or not row['file_path']:
        raise HTTPException(404, "Download not found")

    folder = Path(row['file_path']).resolve()
    _assert_within_upload_folder(folder)

    if not folder.is_dir():
        raise HTTPException(400, "Not a multi-track download")

    track_path = (folder / filename).resolve()

    # Security: resolved path must be inside the folder
    try:
        track_path.relative_to(folder)
    except ValueError:
        raise HTTPException(400, "Invalid track path")

    _assert_within_upload_folder(track_path)

    if not track_path.is_file():
        raise HTTPException(404, "Track file not found")

    name_ascii = track_path.name.encode('ascii', 'replace').decode()
    name_encoded = _url_quote(track_path.name.encode('utf-8'), safe='')
    content_disposition = (
        f"attachment; filename=\"{name_ascii}\"; filename*=UTF-8''{name_encoded}"
    )

    return FileResponse(
        str(track_path),
        headers={"Content-Disposition": content_disposition},
    )


@app.get("/api/queue")
async def queue():
    return get_queue()


@app.get("/api/history")
async def history(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: str = Query(""),
    sort_by: str = Query("completed_at"),
    sort_dir: str = Query("desc"),
    platform: str = Query(""),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
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
        if date_from:
            where.append("DATE(completed_at) >= ?")
            params.append(date_from)
        if date_to:
            where.append("DATE(completed_at) <= ?")
            params.append(date_to)

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

        # Flag rows whose file still exists on disk so the UI can offer a
        # download button only for downloads that weren't cleaned up yet.
        dl_ids = [i['download_id'] for i in items if i.get('download_id')]
        dl_rows = {}
        if dl_ids:
            placeholders = ','.join('?' * len(dl_ids))
            for r in db.execute(
                f"SELECT id, file_path, status FROM downloads WHERE id IN ({placeholders})",
                dl_ids,
            ):
                dl_rows[r['id']] = r
        for item in items:
            r = dl_rows.get(item.get('download_id'))
            item['available'] = bool(
                r and r['status'] == 'completed' and r['file_path']
                and Path(r['file_path']).exists()
            )

        return {
            'items': items,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
        }
    finally:
        db.close()


@app.delete("/api/history", dependencies=[Depends(require_token)])
async def clear_history():
    db = get_db()
    try:
        db.execute("DELETE FROM history")
        db.commit()
    finally:
        db.close()
    return {"cleared": True}


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

        rows = db.execute("""
            SELECT DATE(completed_at) as day, COUNT(*) as count
            FROM history
            WHERE DATE(completed_at) >= DATE('now', '-6 days')
            GROUP BY DATE(completed_at)
        """).fetchall()
        day_map = {r['day']: r['count'] for r in rows}
        activity = [
            {'date': (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d'),
             'count': day_map.get((datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d'), 0)}
            for i in range(6, -1, -1)
        ]

        top_tracks = db.execute(
            """SELECT title, artist, COUNT(*) as downloads
               FROM history GROUP BY title, artist
               ORDER BY downloads DESC LIMIT 5"""
        ).fetchall()

        download_dir = Path(settings.UPLOAD_FOLDER)
        loop = asyncio.get_running_loop()

        def _calc_storage():
            return sum(
                f.stat().st_size for f in download_dir.rglob('*')
                if f.is_file() and not f.name.startswith('.')
                and not f.name.startswith('_tmp_')
                and '.cover_cache' not in f.parts
            ) if download_dir.exists() else 0

        total_storage = await loop.run_in_executor(None, _calc_storage)

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


@app.post("/api/backup", dependencies=[Depends(require_token)])
async def backup(req: BackupRequest):
    result = await create_backup(req.selection, req.date_from, req.date_to)
    return result


@app.get("/api/backup")
async def list_backups():
    return get_backup_list()


@app.delete("/api/backup/{backup_id}", dependencies=[Depends(require_token)])
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

    backup_root = Path(settings.BACKUP_FOLDER).resolve()
    backup_path = Path(row['path']).resolve()
    try:
        backup_path.relative_to(backup_root)
    except ValueError:
        raise HTTPException(403, "Access denied")

    if not backup_path.exists():
        raise HTTPException(404, "Backup file missing")

    return FileResponse(path=str(backup_path), filename=row['filename'], media_type='application/zip')


@app.get("/api/thumbnail")
async def thumbnail_proxy(url: str = Query(...), request: Request = None):
    from urllib.parse import urlparse

    ip = _get_client_ip(request) if request else "unknown"
    if not _rate_check(ip, limit=120, window=60):
        raise HTTPException(429, "Too many requests")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.netloc not in ALLOWED_THUMBNAIL_HOSTS:
        raise HTTPException(400, "URL not permitted")

    try:
        origin = f"{parsed.scheme}://{parsed.netloc}"

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

        session = _http_session
        if not session:
            raise HTTPException(503, "Service not ready")
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                app_logger.warning(f"Thumbnail proxy: upstream {resp.status} for {url}")
                raise HTTPException(502, f"Could not load thumbnail (upstream: {resp.status})")

            content_type = resp.headers.get('Content-Type', '').split(';')[0].strip()
            if not content_type.startswith('image/'):
                app_logger.warning(
                    f"Thumbnail proxy: upstream returned non-image "
                    f"content-type {content_type!r} for {url}"
                )
                raise HTTPException(502, "Upstream returned non-image content")

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


@app.post("/api/cover/upload")
async def upload_cover(file: UploadFile = File(...), request: Request = None):
    ip = _get_client_ip(request) if request else "unknown"
    if not _rate_check(ip, limit=20, window=60):
        raise HTTPException(429, "Too many uploads — please wait")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    data = await file.read()
    if len(data) > settings.max_upload_size_bytes:
        raise HTTPException(400, f"Image must be under {settings.MAX_UPLOAD_SIZE_MB} MB")

    CUSTOM_COVER_DIR.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix.lower() if file.filename else ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"

    cover_id = str(uuid.uuid4())
    dest = CUSTOM_COVER_DIR / f"{cover_id}{ext}"
    dest.write_bytes(data)

    return {"cover_id": cover_id, "filename": file.filename or "image"}


@app.get("/api/cover/upload/{cover_id}")
async def serve_cover(cover_id: str):
    if not is_valid_uuid(cover_id):
        raise HTTPException(400, "Invalid cover_id")
    cover_dir = CUSTOM_COVER_DIR.resolve()
    matches = list(cover_dir.glob(f"{cover_id}.*"))
    if not matches:
        raise HTTPException(404, "Cover not found")
    match_path = matches[0].resolve()
    # Belt-and-suspenders path containment check after UUID validation
    _assert_within_cover_dir(match_path)
    return FileResponse(str(match_path))


@app.post("/api/cover/preview")
async def cover_preview(req: CoverPreviewRequest, request: Request = None):
    from backend.image_processor import (
        download_cover_image, process_cover_image, get_target_dimensions
    )
    import imghdr

    ip = _get_client_ip(request) if request else "unknown"
    if not _rate_check(ip, limit=20, window=60):
        raise HTTPException(429, "Too many requests — please wait")

    if not req.thumbnail_url and not req.cover_id:
        raise HTTPException(400, "Provide thumbnail_url or cover_id")

    cover = None
    tmp_path = None

    if req.cover_id:
        cover_dir = CUSTOM_COVER_DIR.resolve()
        matches = list(cover_dir.glob(f"{req.cover_id}.*"))
        if not matches:
            raise HTTPException(404, "Uploaded cover not found")
        match_path = matches[0].resolve()
        _assert_within_cover_dir(match_path)
        cover = str(match_path)
    else:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_f:
            tmp_path = tmp_f.name

    try:
        if not cover:
            cover = await download_cover_image(req.thumbnail_url, tmp_path)
        if not cover:
            raise HTTPException(400, "Could not download thumbnail")

        processed = await process_cover_image(cover, req.ratio, req.resolution)
        w, h = get_target_dimensions(req.ratio, req.resolution)

        import base64
        with open(processed, 'rb') as f:
            raw = f.read()
            b64 = base64.b64encode(raw).decode()

        ext = os.path.splitext(processed)[1].lower()
        mime_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.png': 'image/png', '.webp': 'image/webp', '.gif': 'image/gif'}
        mime_type = mime_map.get(ext)
        if not mime_type:
            detected = imghdr.what(None, h=raw[:32])
            mime_type = f'image/{detected}' if detected else 'image/jpeg'

        size = os.path.getsize(processed)

        # 'original' ratio or resolution skips the fixed-dim calc; read the
        # actual dimensions back from the file so the preview shows real W×H.
        if not w:
            try:
                from PIL import Image as _PILImage
                with _PILImage.open(processed) as _im:
                    w, h = _im.size
            except Exception:
                w, h = None, None

        return {
            'preview': f"data:{mime_type};base64,{b64}",
            'dimensions': f"{w}x{h}" if w else "original",
            'size': format_file_size(size),
        }
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


frontend_path = Path(__file__).parent.parent / "frontend"

if frontend_path.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_path / "assets")), name="assets")
    app.mount("/components", StaticFiles(directory=str(frontend_path / "components")), name="components")

    @app.get("/styles.css")
    async def styles():
        return FileResponse(str(frontend_path / "styles.css"), media_type="text/css")

    @app.get("/app.js")
    async def appjs():
        return FileResponse(str(frontend_path / "app.js"), media_type="application/javascript")

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str = ""):
        return FileResponse(str(frontend_path / "index.html"))
