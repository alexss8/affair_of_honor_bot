import asyncio
import datetime
import logging
import time
from unittest.mock import AsyncMock, patch
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from config import Config
from engine import DuelEngine
from handlers import router
from queued_repo import QueuedRepository
from repository import SQLiteRepository

async def test_500_concurrent_handles(tmp_path):
    sqlite_repo = SQLiteRepository(str(tmp_path / "load_test.db"))
    logger = logging.getLogger("load_test")
    repo = QueuedRepository(target_repo=sqlite_repo, logger=logger)
    repo.init()
    repo.start_worker()

    config = Config(bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", default_hp=1, top_limit=500)
    engine = DuelEngine(repo=repo, logger=logger, default_hp=1)

    bot = AsyncMock(spec=Bot)
    bot.id = 999
    dp = Dispatcher()
    dp["config"] = config
    dp["repo"] = repo
    dp["engine"] = engine
    dp["logger"] = logger
    dp.include_router(router)

    try:
        chat = Chat(id=-100999, type="supergroup", title="Big Chat 500")
        pairs_count = 500

        updates = []
        for i in range(pairs_count):
            p1_id = 10000 + i * 2
            p2_id = 10000 + i * 2 + 1
            msg_id = 50000 + i

            session = engine.start_duel(chat.id, msg_id, p1_id, f"Attacker_{i}", p2_id, f"Defender_{i}")
            session.p1.choice = "top"

            defender_user = User(id=p2_id, is_bot=False, first_name=f"Defender_{i}")
            msg = Message(message_id=msg_id, date=datetime.datetime.now(), chat=chat, text="Battle")
            cb = CallbackQuery(id=f"cb_{i}", from_user=defender_user, chat_instance="123", message=msg, data="def_bot")
            updates.append(Update(update_id=i + 1, callback_query=cb))

        start_time = time.perf_counter()
        with patch("handlers.asyncio.sleep", new=AsyncMock()):
            await asyncio.gather(*(dp.feed_update(bot, upd) for upd in updates))
        await repo.stop_worker()
        elapsed = time.perf_counter() - start_time

        print(f"\n[LOAD TEST] Обработано {pairs_count} дуэлей с фоновой очередью за {elapsed:.3f} сек.")

        assert len(engine.sessions) == 0
        top = repo.get_top(chat.id, limit=500)
        assert len(top) == 500
        assert elapsed < 5.0
    finally:
        await repo.stop_worker()
        router._parent_router = None
