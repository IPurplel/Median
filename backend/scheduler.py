import asyncio
import subprocess
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from backend.config import settings
from backend.db_models import get_db
from backend.utils.cache_manager import metadata_cache
from backend.logger import app_logger

_CLEANUP_BATCH_SIZE = 500

scheduler = AsyncIOScheduler(timezone="UTC")


async def cleanup_old_downloads():
    db = get_db()
    try:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(minutes=settings.CLEANUP_INTERVAL)).strftime('%Y-%m-%d %H:%M:%S')

        rows = db.execute("""
            SELECT id, file_path FROM downloads
            WHERE status = 'completed'
            AND keep_file = 0
            AND completed_at < ?
            LIMIT ?
        """, (cutoff, _CLEANUP_BATCH_SIZE)).fetchall()

        deleted = 0
        cleaned_ids = []
        for row in rows:
            file_path = row['file_path']
            deletion_ok = False
            if file_path:
                path = Path(file_path)
                try:
                    if path.is_file():
                        path.unlink()
                        deletion_ok = True
                        deleted += 1
                    elif path.is_dir():
                        shutil.rmtree(str(path), ignore_errors=True)
                        deletion_ok = True
                        deleted += 1
                    elif not path.exists():
                        deletion_ok = True
                except Exception as e:
                    app_logger.error(f"Cleanup error for {file_path}: {e}")
            else:
                deletion_ok = True

            if deletion_ok:
                cleaned_ids.append(row['id'])

        if cleaned_ids:
            placeholders = ','.join('?' * len(cleaned_ids))
            db.execute(
                f"UPDATE downloads SET status = 'cleaned' WHERE id IN ({placeholders})",
                cleaned_ids
            )

        db.commit()
        if deleted > 0:
            app_logger.info(f"Cleanup: deleted {deleted} download(s)")

    except Exception as e:
        app_logger.error(f"Cleanup job error: {e}")
    finally:
        db.close()


_STALE_PARTIAL_AGE = timedelta(hours=1)


def _sweep_stale_partials() -> int:
    """Walk the download folder and delete stale leftovers. Blocking."""
    from backend.downloader import PARTIAL_SUFFIXES

    root = Path(settings.UPLOAD_FOLDER)
    if not root.exists():
        return 0

    cutoff = datetime.now().timestamp() - _STALE_PARTIAL_AGE.total_seconds()
    removed = 0
    try:
        for entry in root.iterdir():
            try:
                if entry.is_file():
                    if ((entry.name.startswith('_tmp_')
                         or entry.suffix.lower() in PARTIAL_SUFFIXES)
                            and entry.stat().st_mtime < cutoff):
                        entry.unlink()
                        removed += 1
                elif entry.is_dir() and entry.name.startswith('_concat_'):
                    mtimes = [entry.stat().st_mtime]
                    mtimes += [f.stat().st_mtime for f in entry.rglob('*')]
                    if max(mtimes) < cutoff:
                        shutil.rmtree(str(entry), ignore_errors=True)
                        removed += 1
                elif entry.is_dir():
                    # Album folders: remove stale partials, keep finished tracks
                    for f in entry.rglob('*'):
                        if (f.is_file()
                                and f.suffix.lower() in PARTIAL_SUFFIXES
                                and f.stat().st_mtime < cutoff):
                            f.unlink()
                            removed += 1
            except OSError as e:
                app_logger.warning(f"Stale-partial sweep error for {entry}: {e}")
    except Exception as e:
        app_logger.error(f"Stale-partial sweep failed: {e}")

    return removed


async def cleanup_stale_partials():
    """Sweep orphaned yt-dlp leftovers from the download folder.

    Catches what per-download cleanup can't: cancelled downloads whose
    executor thread kept writing after the partials were deleted, and
    leftovers from crashes or versions before cleanup existed. Only entries
    untouched for _STALE_PARTIAL_AGE are removed, so in-flight downloads
    (which keep updating mtimes) are never raced.

    The walk runs in a worker thread — over a large library it is slow enough
    to stall progress streams and the container healthcheck otherwise.
    """
    loop = asyncio.get_running_loop()
    removed = await loop.run_in_executor(None, _sweep_stale_partials)
    if removed:
        app_logger.info(f"Stale-partial sweep: removed {removed} leftover(s)")


async def cleanup_cache():
    try:
        metadata_cache.cleanup_expired()
    except Exception as e:
        app_logger.error(f"Cache cleanup error: {e}")


async def update_yt_dlp():
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, lambda: subprocess.run(
            ['pip', 'install', '--upgrade', 'yt-dlp', '--quiet', '--break-system-packages'],
            capture_output=True, text=True, timeout=120
        ))
        if result.returncode == 0:
            app_logger.info("yt-dlp updated successfully")
        else:
            result2 = await loop.run_in_executor(None, lambda: subprocess.run(
                ['pip', 'install', '--upgrade', 'yt-dlp', '--quiet'],
                capture_output=True, text=True, timeout=120
            ))
            if result2.returncode == 0:
                app_logger.info("yt-dlp updated successfully")
            else:
                app_logger.warning(f"yt-dlp update failed: {result2.stderr}")
    except Exception as e:
        app_logger.error(f"yt-dlp update error: {e}")


async def vacuum_database():
    db = get_db()
    try:
        db.execute("VACUUM")
        db.commit()
        app_logger.info("Database vacuum complete")
    except Exception as e:
        app_logger.error(f"Database vacuum error: {e}")
    finally:
        db.close()


async def rotate_logs():
    log_dir = Path(settings.LOG_FOLDER)
    if not log_dir.exists():
        return

    cutoff = datetime.now() - timedelta(days=settings.LOG_BACKUP_COUNT)
    for log_file in log_dir.glob('*.log.*'):
        try:
            if datetime.fromtimestamp(log_file.stat().st_mtime) < cutoff:
                log_file.unlink()
        except Exception as e:
            app_logger.warning(f"Could not remove log file {log_file}: {e}")


async def prune_history():
    if not settings.HISTORY_RETENTION_DAYS:
        return
    db = get_db()
    try:
        db.execute(
            "DELETE FROM history WHERE completed_at < datetime('now', ?)",
            (f'-{settings.HISTORY_RETENTION_DAYS} days',)
        )
        db.commit()
        app_logger.info(f"Pruned history older than {settings.HISTORY_RETENTION_DAYS} days")
    except Exception as e:
        app_logger.error(f"History prune error: {e}")
    finally:
        db.close()


def start_scheduler():
    scheduler.add_job(
        cleanup_old_downloads,
        IntervalTrigger(minutes=settings.CLEANUP_INTERVAL),
        id='cleanup_downloads',
        replace_existing=True
    )

    scheduler.add_job(
        cleanup_stale_partials,
        IntervalTrigger(minutes=30),
        id='cleanup_stale_partials',
        replace_existing=True,
        # Run once right at startup so leftovers from before this fix
        # (or from a crash) get cleaned on boot.
        next_run_time=datetime.now(timezone.utc),
    )

    scheduler.add_job(
        cleanup_cache,
        IntervalTrigger(hours=1),
        id='cleanup_cache',
        replace_existing=True
    )

    scheduler.add_job(
        update_yt_dlp,
        IntervalTrigger(hours=settings.AUTO_UPDATE_INTERVAL),
        id='update_yt_dlp',
        replace_existing=True
    )

    scheduler.add_job(
        vacuum_database,
        IntervalTrigger(days=30),
        id='vacuum_db',
        replace_existing=True
    )

    scheduler.add_job(
        rotate_logs,
        IntervalTrigger(days=1),
        id='rotate_logs',
        replace_existing=True
    )

    scheduler.add_job(
        prune_history,
        IntervalTrigger(days=1),
        id='prune_history',
        replace_existing=True
    )

    scheduler.start()
    app_logger.info("Scheduler started with all background jobs")
