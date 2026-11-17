import logging
from logging.handlers import RotatingFileHandler

MAX_LOG_BYTES = 512 * 1024  # 512 КБ на основной файл логов
BACKUP_COUNT = 1            # Хранить только 1 старый файл (итого макс. 1 МБ на диске)

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
ERROR_MARKER = "| ERROR |"


def make_formatter() -> logging.Formatter:
    return logging.Formatter(LOG_FORMAT)


def is_error_line(line: str) -> bool:
    """Проверяет, что строка лога записана с уровнем ERROR (по формату LOG_FORMAT)."""
    return ERROR_MARKER in line


def setup_logger(log_file: str = "bot.log") -> logging.Logger:
    logger = logging.getLogger("duel_bot")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        formatter = make_formatter()

        # Запись в файл с жесткой ротацией (макс 512 КБ + 1 бэкап = до 1 МБ)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)

        # Вывод в консоль (для systemd)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)

    return logger
