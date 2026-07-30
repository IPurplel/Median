import os
from typing import Literal
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    PORT: int = 5000
    UPLOAD_FOLDER: str = "/app/downloads"
    BACKUP_FOLDER: str = "/app/backups"
    LOG_FOLDER: str = "/app/logs"
    DATABASE_PATH: str = "/app/database/median.db"
    CUSTOM_COVER_DIR: str = "/tmp/median_covers"

    MAX_UPLOAD_SIZE_MB: int = 20
    MAX_URL_LENGTH: int = 2048
    MAX_PLAYLIST_TRACKS: int = 500
    # Ceiling on "download the artist's whole discography" — one queued
    # download per album, so this caps how much a single click can enqueue.
    MAX_DISCOGRAPHY_ALBUMS: int = 100
    # Discography albums are exempt from auto-cleanup until the combined zip is
    # collected, otherwise the earliest ones expire while the last are still
    # downloading. This releases an uncollected batch anyway, counted from when
    # its LAST album finished — never from when it was queued, which would drop
    # the hold mid-run and reintroduce the very problem it prevents.
    BATCH_HOLD_HOURS: int = 3
    # Once the combined zip has been collected the server's copies are
    # redundant, so they go on a short timer instead of the normal retention
    # window — a discography is several gigabytes worth reclaiming promptly.
    BATCH_DELETE_MINUTES: int = 3
    CORS_ORIGINS: str = "*"

    # Does double duty: how often the cleanup job runs AND how old a completed
    # download must be to be swept. Real retention therefore lands between one
    # and two times this value. Long enough that a multi-album batch finishes
    # before its first album expires.
    CLEANUP_INTERVAL: int = 90
    AUTO_UPDATE_INTERVAL: int = 48
    DOWNLOAD_CHUNK_SIZE: int = 5
    LOG_BACKUP_COUNT: int = 7
    LOG_FORMAT: Literal["text", "json"] = "text"
    COVER_DEFAULT_RATIO: str = "1:1"
    COVER_DEFAULT_RESOLUTION: str = "original"
    COVER_CACHE_MAX_MB: int = 500

    BLURRY_PADDING_BLUR_RADIUS: int = 40
    BLURRY_PADDING_ENABLED: bool = True

    CONCATENATION_VALIDATE_BEFORE: bool = True
    CONCATENATION_CREATE_CHAPTERS: bool = True

    BACKUP_COMPRESSION_LEVEL: int = 6

    MEDIAN_API_TOKEN: str = ""
    MAX_CONCURRENT_DOWNLOADS: int = 3
    HISTORY_RETENTION_DAYS: int = 90

    VIDEO_CODEC_H264: str = "libx264"
    VIDEO_PRESET: str = "fast"
    VIDEO_TUNE_STILL: str = "stillimage"
    VIDEO_CRF: int = 18
    AUDIO_CODEC_AAC: str = "aac"
    AUDIO_BITRATE_DEFAULT: str = "192k"

    CONCAT_AUDIO_TIMEOUT: int = 300
    CONCAT_VIDEO_TIMEOUT: int = 600
    COVER_MERGE_TIMEOUT: int = 1800

    # Cover+Audio renders a video from a single static image. A high frame rate
    # just multiplies identical frames — at 25fps a 60-min album is ~90k frames,
    # which overruns the merge timeout. 1fps is plenty for a still cover (album
    # art shows via the covr atom / sidecar cover.jpg, not the video stream).
    COVER_VIDEO_FPS: float = 1.0

    # Crossfade ("Merge into single file" blend between adjacent tracks).
    # Crossfade forces a re-encode (incompatible with the -c copy fast path),
    # so the concat timeouts above apply to it too.
    CROSSFADE_DURATION: float = 2.0          # default overlap seconds
    CROSSFADE_MIN_DURATION: float = 0.5      # UI/clamp floor
    CROSSFADE_MAX_DURATION: float = 12.0     # UI/clamp ceiling
    CROSSFADE_CURVE: str = "qsin"            # constant-power crossfade curve
    CROSSFADE_VIDEO_TRANSITION: str = "fade"  # xfade transition (smooth dissolve)
    CROSSFADE_VIDEO_FPS: int = 30            # common fps for xfade inputs
    CROSSFADE_FALLBACK_WIDTH: int = 1920     # canvas when clip resolutions differ
    CROSSFADE_FALLBACK_HEIGHT: int = 1080
    CROSSFADE_NORMALIZE_SAMPLE_RATE: int = 44100
    CROSSFADE_MAX_TRACKS: int = 50           # above this, fall back to hard-cut concat

    ALLOWED_THUMBNAIL_HOSTS: set = {
        "i.ytimg.com", "i9.ytimg.com",
        "i1.sndcdn.com", "i2.sndcdn.com",
        "f4.bcbits.com",
    }

    @property
    def cors_origins_list(self) -> list:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.CORS_ORIGINS.split(',') if o.strip()]

    @property
    def custom_cover_path(self) -> Path:
        return Path(self.CUSTOM_COVER_DIR)

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()


def validate_settings():
    errors = []
    if settings.CLEANUP_INTERVAL <= 0:
        errors.append("CLEANUP_INTERVAL must be positive")
    if settings.MAX_CONCURRENT_DOWNLOADS <= 0:
        errors.append("MAX_CONCURRENT_DOWNLOADS must be positive")
    if settings.MAX_UPLOAD_SIZE_MB <= 0:
        errors.append("MAX_UPLOAD_SIZE_MB must be positive")
    if settings.MAX_URL_LENGTH < 64:
        errors.append("MAX_URL_LENGTH must be at least 64")
    if settings.MAX_PLAYLIST_TRACKS <= 0:
        errors.append("MAX_PLAYLIST_TRACKS must be positive")
    if settings.MAX_DISCOGRAPHY_ALBUMS <= 0:
        errors.append("MAX_DISCOGRAPHY_ALBUMS must be positive")
    if settings.BATCH_HOLD_HOURS <= 0:
        errors.append("BATCH_HOLD_HOURS must be positive")
    if settings.BATCH_DELETE_MINUTES <= 0:
        errors.append("BATCH_DELETE_MINUTES must be positive")
    if errors:
        raise ValueError("Invalid configuration:\n" + "\n".join(f"  - {e}" for e in errors))


def ensure_directories():
    for folder in [settings.UPLOAD_FOLDER, settings.BACKUP_FOLDER, settings.LOG_FOLDER]:
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
        except PermissionError:
            pass

    try:
        Path(settings.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        pass


try:
    ensure_directories()
except Exception:
    pass
