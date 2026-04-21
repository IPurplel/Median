import sqlite3
import json
from datetime import datetime
from pathlib import Path
from backend.config import settings


def get_db():
    db = sqlite3.connect(settings.DATABASE_PATH, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA page_size=4096")
    db.execute("PRAGMA cache_size=-64000")
    db.execute("PRAGMA synchronous=NORMAL")
    return db


def init_db():
    db = get_db()
    cursor = db.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS downloads (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            platform TEXT,
            title TEXT,
            artist TEXT,
            album TEXT,
            duration INTEGER,
            thumbnail_url TEXT,
            download_type TEXT,
            format TEXT,
            bitrate TEXT,
            status TEXT DEFAULT 'pending',
            progress REAL DEFAULT 0,
            speed TEXT,
            eta TEXT,
            file_path TEXT,
            file_size INTEGER,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            is_playlist INTEGER DEFAULT 0,
            playlist_count INTEGER,
            is_concatenated INTEGER DEFAULT 0,
            cover_settings TEXT,
            chapter_data TEXT,
            keep_file INTEGER DEFAULT 0,
            queue_position INTEGER
        );

        CREATE TABLE IF NOT EXISTS queue (
            id TEXT PRIMARY KEY,
            download_id TEXT,
            position INTEGER,
            status TEXT DEFAULT 'waiting',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (download_id) REFERENCES downloads(id)
        );

        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            download_id TEXT,
            url TEXT,
            title TEXT,
            artist TEXT,
            platform TEXT,
            format TEXT,
            file_size INTEGER,
            completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            redownload_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS metadata_cache (
            url TEXT PRIMARY KEY,
            data TEXT,
            cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ttl INTEGER DEFAULT 86400
        );

        CREATE TABLE IF NOT EXISTS statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            platform TEXT,
            artist TEXT,
            download_count INTEGER DEFAULT 1,
            total_size INTEGER DEFAULT 0,
            avg_speed REAL
        );

        CREATE TABLE IF NOT EXISTS backups (
            id TEXT PRIMARY KEY,
            filename TEXT,
            path TEXT,
            size INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            file_count INTEGER,
            date_range TEXT
        );

        CREATE TABLE IF NOT EXISTS watched_urls (
            url TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);
        CREATE INDEX IF NOT EXISTS idx_downloads_created ON downloads(created_at);
        CREATE INDEX IF NOT EXISTS idx_history_completed ON history(completed_at);
        CREATE INDEX IF NOT EXISTS idx_statistics_date ON statistics(date);
    """)

    db.commit()
    db.close()


def row_to_dict(row):
    if row is None:
        return None
    return dict(row)
