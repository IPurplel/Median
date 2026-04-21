import asyncio
import subprocess
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from backend.config import settings
from backend.db_models import get_db
from backend.utils.cache_manager import metadata_cache
from backend.logger import app_logger


scheduler = AsyncIOScheduler(timezone="UTC")


async def cleanup_old_downloads():
    """Delete downloads older than CLEANUP_INTERVAL minutes."""
    db = get_db()
    try:
        # Use UTC to match SQLite's datetime('now') which is always UTC
        cutoff = datetime.utcnow() - timedelta(minutes=settings.CLEANUP_INTERVAL)
        cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')

        rows = db.execute("""
            SELECT id, file_path, keep_file FROM downloads
            WHERE status = 'completed'
            AND completed_at < ?
            AND keep_file = 0
        """, (cutoff_str,)).fetchall()

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
                        # File already gone — treat as cleaned
                        deletion_ok = True
                except Exception as e:
                    app_logger.error(f"Cleanup error for {file_path}: {e}")
            else:
                # No file path recorded — nothing to delete
                deletion_ok = True

            # Bug fix: only mark 'cleaned' if deletion actually succeeded
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


async def cleanup_cache():
    """Remove expired metadata cache entries."""
    try:
        metadata_cache.cleanup_expired()
    except Exception as e:
        app_logger.error(f"Cache cleanup error: {e}")


async def update_yt_dlp():
    """Auto-update yt-dlp."""
    try:
        result = subprocess.run(
            ['pip', 'install', '--upgrade', 'yt-dlp', '--quiet',
             '--break-system-packages'],  # Required in Alpine/Debian externally-managed envs
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            app_logger.info("yt-dlp updated successfully")
        else:
            # Retry without the flag (non-managed environments)
            result2 = subprocess.run(
                ['pip', 'install', '--upgrade', 'yt-dlp', '--quiet'],
                capture_output=True, text=True, timeout=120
            )
            if result2.returncode == 0:
                app_logger.info("yt-dlp updated successfully")
            else:
                app_logger.warning(f"yt-dlp update failed: {result2.stderr}")
    except Exception as e:
        app_logger.error(f"yt-dlp update error: {e}")


async def vacuum_database():
    """Periodic database vacuum for maintenance."""
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
    """Remove old log files."""
    log_dir = Path(settings.LOG_FOLDER)
    if not log_dir.exists():
        return

    cutoff = datetime.now() - timedelta(days=settings.LOG_RETENTION_DAYS)
    for log_file in log_dir.glob('*.log.*'):
        try:
            if datetime.fromtimestamp(log_file.stat().st_mtime) < cutoff:
                log_file.unlink()
        except Exception:
            pass


def start_scheduler():
    """Initialize and start all scheduled jobs."""
    # Cleanup downloads every CLEANUP_INTERVAL minutes
    scheduler.add_job(
        cleanup_old_downloads,
        IntervalTrigger(minutes=settings.CLEANUP_INTERVAL),
        id='cleanup_downloads',
        replace_existing=True
    )

    # Cache cleanup every hour
    scheduler.add_job(
        cleanup_cache,
        IntervalTrigger(hours=1),
        id='cleanup_cache',
        replace_existing=True
    )

    # Update yt-dlp every AUTO_UPDATE_INTERVAL hours
    scheduler.add_job(
        update_yt_dlp,
        IntervalTrigger(hours=settings.AUTO_UPDATE_INTERVAL),
        id='update_yt_dlp',
        replace_existing=True
    )

    # Database vacuum monthly
    scheduler.add_job(
        vacuum_database,
        IntervalTrigger(days=30),
        id='vacuum_db',
        replace_existing=True
    )

    # Log rotation daily
    scheduler.add_job(
        rotate_logs,
        IntervalTrigger(days=1),
        id='rotate_logs',
        replace_existing=True
    )

    scheduler.start()
    app_logger.info("Scheduler started with all background jobs")
