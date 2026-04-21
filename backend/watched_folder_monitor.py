import asyncio
import os
from pathlib import Path
from typing import Set
from backend.config import settings
from backend.db_models import get_db
from backend.utils.validators import validate_url
from backend.logger import app_logger

WATCHED_FILE = Path(settings.WATCHED_FOLDER) / "watched_urls.txt"
_monitoring = False
_queued_urls: Set[str] = set()
_on_new_url = None  # Callback to queue download


def set_url_callback(callback):
    global _on_new_url
    _on_new_url = callback


def _load_processed_urls() -> Set[str]:
    """Load URLs that should not be re-queued (excludes 'failed' so they get retried)."""
    db = get_db()
    try:
        rows = db.execute(
            "SELECT url FROM watched_urls WHERE status != 'failed'"
        ).fetchall()
        return {r['url'] for r in rows}
    finally:
        db.close()


def _mark_url_processed(url: str, status: str = 'processed'):
    db = get_db()
    try:
        db.execute("""
            INSERT OR REPLACE INTO watched_urls (url, status, processed_at)
            VALUES (?, ?, datetime('now'))
        """, (url, status))
        db.commit()
    finally:
        db.close()


async def check_watched_file() -> list:
    """Check watched_urls.txt for new URLs. Returns list of new URLs added."""
    if not WATCHED_FILE.exists():
        WATCHED_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATCHED_FILE.touch()
        return []

    processed = _load_processed_urls()
    new_urls = []

    try:
        with open(WATCHED_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        for line in lines:
            url = line.strip()
            if not url or url.startswith('#'):
                continue

            if url in processed:
                continue

            is_valid, platform, error = validate_url(url)
            if not is_valid:
                app_logger.warning(f"Watched folder invalid URL: {url} - {error}")
                _mark_url_processed(url, 'invalid')
                continue

            new_urls.append({'url': url, 'platform': platform})
            app_logger.info(f"Watched folder: new URL detected - {url}")

            if _on_new_url:
                try:
                    await _on_new_url(url, platform)
                    # Bug fix: only mark as 'queued' AFTER the callback succeeds
                    _mark_url_processed(url, 'queued')
                except Exception as e:
                    app_logger.error(f"Watched folder callback error: {e}")
                    # Mark as 'failed' so it can be retried on next check
                    _mark_url_processed(url, 'failed')
            else:
                _mark_url_processed(url, 'queued')

    except Exception as e:
        app_logger.error(f"Watched folder check error: {e}")

    return new_urls


async def start_watching():
    """Start the watched folder monitoring loop."""
    global _monitoring
    _monitoring = True
    app_logger.info(f"Watching: {WATCHED_FILE}")

    while _monitoring:
        await check_watched_file()
        await asyncio.sleep(settings.WATCHED_FOLDER_CHECK_INTERVAL)


def stop_watching():
    global _monitoring
    _monitoring = False


def get_watched_status() -> dict:
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM watched_urls ORDER BY added_at DESC LIMIT 50"
        ).fetchall()
        return {
            'watching': _monitoring,
            'file_path': str(WATCHED_FILE),
            'file_exists': WATCHED_FILE.exists(),
            'urls': [dict(r) for r in rows],
        }
    finally:
        db.close()
