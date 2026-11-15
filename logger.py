import logging
from logging.handlers import RotatingFileHandler

def setup_logger(log_file: str = "bot.log") -> logging.Logger:
    logger = logging.getLogger("duel_bot")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

        # Запись в файл с ротацией (макс 5 МБ, хранить 3 старых файла)
        file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)

        # Вывод в консоль (для systemd)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)

    return logger
