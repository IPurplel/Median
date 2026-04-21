import json
import time
from functools import lru_cache
from typing import Optional, Any
from backend.db_models import get_db
from backend.logger import app_logger


class MetadataCache:
    def __init__(self, ttl: int = 86400):
        self.ttl = ttl

    def get(self, url: str) -> Optional[dict]:
        db = get_db()
        try:
            row = db.execute(
                "SELECT data, cached_at, ttl FROM metadata_cache WHERE url = ?",
                (url,)
            ).fetchone()

            if row:
                # SQLite may append fractional seconds — strip before parsing
                cached_str = row['cached_at'].split('.')[0]
                try:
                    ts = time.mktime(time.strptime(cached_str, '%Y-%m-%d %H:%M:%S'))
                except ValueError:
                    ts = time.time()  # Unparseable — treat as fresh to be safe
                age = time.time() - ts
                if age < row['ttl']:
                    return json.loads(row['data'])
                else:
                    db.execute("DELETE FROM metadata_cache WHERE url = ?", (url,))
                    db.commit()
        except Exception as e:
            app_logger.error(f"Cache get error: {e}")
        finally:
            db.close()
        return None

    def set(self, url: str, data: dict, ttl: int = None):
        db = get_db()
        try:
            db.execute(
                """INSERT OR REPLACE INTO metadata_cache (url, data, cached_at, ttl)
                   VALUES (?, ?, datetime('now'), ?)""",
                (url, json.dumps(data), ttl or self.ttl)
            )
            db.commit()
        except Exception as e:
            app_logger.error(f"Cache set error: {e}")
        finally:
            db.close()

    def invalidate(self, url: str):
        db = get_db()
        try:
            db.execute("DELETE FROM metadata_cache WHERE url = ?", (url,))
            db.commit()
        except Exception as e:
            app_logger.error(f"Cache invalidate error: {e}")
        finally:
            db.close()

    def cleanup_expired(self):
        db = get_db()
        try:
            db.execute(
                """DELETE FROM metadata_cache
                   WHERE (julianday('now') - julianday(cached_at)) * 86400 > ttl"""
            )
            db.commit()
        except Exception as e:
            app_logger.error(f"Cache cleanup error: {e}")
        finally:
            db.close()


metadata_cache = MetadataCache()
