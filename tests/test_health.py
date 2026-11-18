import datetime
import logging
import time
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, MessageEntity, Update, User
from helpers import build_dispatcher

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.domain.models import Player
from bot.logger import is_error_line, make_formatter
from bot.services import system_metrics
from bot.storage.queued_repo import QueuedRepository
from bot.storage.sqlite_repo import SQLiteRepository
from bot.telegram.handlers import router


def _write_real_log(log_file) -> None:
    """Пишет лог через тот же форматтер, что и боевой логгер, чтобы тест ловил рассинхрон формата."""
    test_logger = logging.getLogger("health_log_writer")
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(make_formatter())
    test_logger.addHandler(handler)
    try:
        test_logger.info("Start")
        test_logger.warning("Just a warning")
        test_logger.error("Test boom")
    finally:
        test_logger.removeHandler(handler)
        handler.close()


def test_is_error_line_matches_only_error_level(tmp_path):
    log_file = tmp_path / "levels.log"
    _write_real_log(log_file)
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert [is_error_line(line) for line in lines] == [False, False, True]


@pytest.fixture
def health_env(tmp_path):
    db_file = tmp_path / "health_test.db"
    log_file = tmp_path / "health_bot.log"
    _write_real_log(log_file)

    sqlite_repo = SQLiteRepository(str(db_file))
    logger = logging.getLogger("health_test")
    repo = QueuedRepository(target_repo=sqlite_repo, logger=logger)
    repo.init()

    config = Config(
        bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        admin_id=777,
        db_path=str(db_file),
        log_file=str(log_file),
    )
    engine = DuelEngine(logger=logger, default_hp=5)

    bot = AsyncMock(spec=Bot)
    bot.id = 999

    dp = build_dispatcher(config, repo, engine, logger)

    yield dp, bot, repo, engine
    router._parent_router = None


def make_status_update(update_id: int, user_id: int, chat_type: str = "private") -> Update:
    chat_id = user_id if chat_type == "private" else -1001
    chat = Chat(id=chat_id, type=chat_type)
    user = User(id=user_id, is_bot=False, first_name="Tester")
    msg = Message(
        message_id=update_id,
        date=datetime.datetime.now(),
        chat=chat,
        from_user=user,
        text="/status",
        entities=[MessageEntity(type="bot_command", offset=0, length=7)],
    )
    return Update(update_id=update_id, message=msg)


def test_get_uptime_counts_from_given_start():
    assert system_metrics.get_uptime(1000.0, now=1000.0 + 90061) == (1, 1, 1)
    assert system_metrics.get_uptime(0.0, now=59.0) == (0, 0, 0)


async def test_status_uptime_counts_from_bot_start(health_env):
    dp, bot, _, _ = health_env
    dp["started_at"] = time.time() - 3661

    await dp.feed_update(bot, make_status_update(1, user_id=777, chat_type="private"))

    assert "0д 1ч 1м" in str(bot.call_args)


def test_repository_health_stats(health_env):
    _, _, repo, _ = health_env
    repo.record_duel(-1001, Player(1, "A", 5), Player(2, "B", 0))
    repo.save_user(-1001, 1, "alice", "Alice")

    stats = repo.get_health_stats()
    assert stats["total_duels"] == 1
    assert stats["today_duels"] == 1
    assert stats["queue_size"] == 1
    assert stats["db_size_kb"] > 0


async def test_status_command_access_and_output(health_env):
    dp, bot, repo, _ = health_env
    repo.record_duel(-1001, Player(1, "A", 5), Player(2, "B", 0))

    # 1. Чужой пользователь в личке -> игнор
    await dp.feed_update(bot, make_status_update(1, user_id=123, chat_type="private"))
    bot.assert_not_called()

    # 2. Админ в групповом чате -> игнор (работает только в ЛС)
    await dp.feed_update(bot, make_status_update(2, user_id=777, chat_type="supergroup"))
    bot.assert_not_called()

    # 3. Админ в личных сообщениях -> получает отчет
    await dp.feed_update(bot, make_status_update(3, user_id=777, chat_type="private"))
    bot.assert_called_once()
    sent_text = str(bot.call_args)
    assert "СТАТУС БОТА: ОК" in sent_text
    assert "Память процесса (ОЗУ):" in sent_text
    assert "Место на диске (БД + логи):" in sent_text
    assert "Всего дуэлей сыграно: <b>1</b>" in sent_text
    assert "Записей ERROR: <b>1</b>" in sent_text
    assert "Test boom" in sent_text
