import os
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    PORT: int = 5000
    UPLOAD_FOLDER: str = "/app/downloads"
    BACKUP_FOLDER: str = "/app/backups"
    WATCHED_FOLDER: str = "/app/watched"
    LOG_FOLDER: str = "/app/logs"
    DATABASE_PATH: str = "/app/database/median.db"

    CLEANUP_INTERVAL: int = 15  # minutes
    AUTO_UPDATE_INTERVAL: int = 48  # hours
    DOWNLOAD_CHUNK_SIZE: int = 5  # MB
    LOG_RETENTION_DAYS: int = 7
    WATCHED_FOLDER_CHECK_INTERVAL: int = 5  # seconds

    COVER_DEFAULT_RATIO: str = "1:1"
    COVER_DEFAULT_RESOLUTION: str = "medium"
    COVER_CACHE_ENABLED: bool = True
    COVER_CACHE_TTL: int = 86400  # 24 hours

    BLURRY_PADDING_BLUR_RADIUS: int = 40
    BLURRY_PADDING_BLUR_SIGMA: int = 2
    BLURRY_PADDING_ENABLED: bool = True

    CONCATENATION_BUFFER_SIZE: int = 10485760  # 10MB
    CONCATENATION_CHUNK_SIZE: int = 5242880  # 5MB
    CONCATENATION_VALIDATE_BEFORE: bool = True
    CONCATENATION_CREATE_CHAPTERS: bool = True

    FFMPEG_CHAPTERS_ENABLED: bool = True
    FFMPEG_CHAPTERS_FORMAT: str = "id3"

    PREVIEW_THUMBNAIL_SIZE: str = "300x300"
    PREVIEW_CACHE_ENABLED: bool = True

    METADATA_CACHE_TTL: int = 86400
    HISTORY_PAGE_LIMIT: int = 50
    DATABASE_PAGE_SIZE: int = 4096

    BACKUP_COMPRESSION_LEVEL: int = 6

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()


def ensure_directories():
    """
    Create required runtime directories.
    Bug #15 fix: Called explicitly at startup (lifespan) instead of at import time,
    so importing config in non-Docker environments doesn't raise PermissionError.
    """
    for folder in [settings.UPLOAD_FOLDER, settings.BACKUP_FOLDER,
                   settings.WATCHED_FOLDER, settings.LOG_FOLDER]:
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except PermissionError:
            pass  # Running outside Docker — dirs may not be creatable

    try:
        Path(settings.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass


# Attempt directory creation at import for convenience in Docker.
# Failures are silenced — startup will retry via ensure_directories().
try:
    ensure_directories()
except Exception:
    pass
