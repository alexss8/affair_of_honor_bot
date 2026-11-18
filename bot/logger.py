import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler

MAX_LOG_BYTES = 512 * 1024  # 512 КБ на основной файл логов
BACKUP_COUNT = 1  # Хранить только 1 старый файл (итого макс. 1 МБ на диске)

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | chat=%(chat_id)s | %(message)s"
ERROR_MARKER = "| ERROR |"
NO_CHAT = "-"

# Чат обрабатываемого апдейта. Задачи, созданные из обработчика (например, таймер дуэли),
# наследуют значение — так устроены contextvars в asyncio.
_current_chat_id: ContextVar[int | None] = ContextVar("current_chat_id", default=None)


@contextmanager
def log_chat(chat_id: int | None) -> Iterator[None]:
    """Записи лога внутри блока получают chat=<chat_id>."""
    token = _current_chat_id.set(chat_id)
    try:
        yield
    finally:
        _current_chat_id.reset(token)


class ChatContextFormatter(logging.Formatter):
    """LOG_FORMAT с chat_id текущего апдейта (или «-» вне обработки апдейта)."""

    def format(self, record: logging.LogRecord) -> str:
        chat_id = _current_chat_id.get()
        record.chat_id = NO_CHAT if chat_id is None else chat_id
        return super().format(record)


def make_formatter() -> logging.Formatter:
    return ChatContextFormatter(LOG_FORMAT)


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
