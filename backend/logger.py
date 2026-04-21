import logging
import logging.handlers
from pathlib import Path
from backend.config import settings


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # Avoid duplicate handlers on module reload

    logger.setLevel(logging.DEBUG)

    # ── Console handler ───────────────────────────────────────────────────
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%H:%M:%S'
    ))

    # ── File handler — size-based rotation ───────────────────────────────
    # NOTE: TimedRotatingFileHandler does NOT accept maxBytes; that argument
    # belongs to RotatingFileHandler. Using RotatingFileHandler for size cap.
    log_path = Path(settings.LOG_FOLDER) / "median.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        str(log_path),
        maxBytes=50 * 1024 * 1024,      # 50 MB per file
        backupCount=settings.LOG_RETENTION_DAYS,
        encoding='utf-8',
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
    ))

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


app_logger = setup_logger("median")
