import logging
import logging.handlers
from pathlib import Path
from backend.config import settings


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%H:%M:%S'
    ))

    log_path = Path(settings.LOG_FOLDER) / "median.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if settings.LOG_FORMAT == "json":
        try:
            from pythonjsonlogger import jsonlogger
            file_formatter = jsonlogger.JsonFormatter(
                '%(asctime)s %(levelname)s %(name)s %(message)s'
            )
        except ImportError:
            file_formatter = logging.Formatter(
                '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
            )
    else:
        file_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
        )

    file_handler = logging.handlers.TimedRotatingFileHandler(
        str(log_path),
        when='midnight',
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding='utf-8',
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


app_logger = setup_logger("median")
