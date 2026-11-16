import datetime
import logging
from unittest.mock import AsyncMock
import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, MessageEntity, Update, User

from config import Config
from engine import DuelEngine
from handlers import router
from models import Player
from queued_repo import QueuedRepository
from repository import SQLiteRepository

@pytest.fixture
def health_env(tmp_path):
    db_file = tmp_path / "health_test.db"
    log_file = tmp_path / "health_bot.log"
    log_file.write_text("2026-11-15 12:00:00 [INFO] Start\n2026-11-15 12:05:00 [ERROR] Test boom\n", encoding="utf-8")

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
    engine = DuelEngine(repo=repo, logger=logger, default_hp=5)

    bot = AsyncMock(spec=Bot)
    bot.id = 999

    dp = Dispatcher()
    dp["config"] = config
    dp["repo"] = repo
    dp["engine"] = engine
    dp["logger"] = logger
    dp.include_router(router)

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
